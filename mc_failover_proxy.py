#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import random
import signal
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

DEFAULT_CONFIG_PATH = Path("config.toml")
VALID_HEALTH_CHECK_MODES = {"tcp", "minecraft_status"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
MAX_VARINT_BYTES = 5
MAX_STATUS_JSON_BYTES = 262144
MAX_PACKET_BYTES = MAX_STATUS_JSON_BYTES + 4096

log = logging.getLogger("mc-failover")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ProxyConfig:
    listen_host: str
    listen_port: int


@dataclass(frozen=True)
class TargetConfig:
    host: str
    port: int


@dataclass(frozen=True)
class HealthCheckConfig:
    mode: str
    interval_seconds: float
    timeout_seconds: float
    fail_after: int
    recover_after: int
    target_host: Optional[str]
    target_port: Optional[int]
    protocol_version: int
    status_hostname: Optional[str]
    require_valid_json: bool
    log_status_details: bool
    jitter_seconds: float


@dataclass(frozen=True)
class HealthCheckResult:
    ok: bool
    reason: str
    latency_ms: Optional[float] = None
    version_name: Optional[str] = None
    players_online: Optional[int] = None
    players_max: Optional[int] = None


@dataclass(frozen=True)
class ConnectionConfig:
    timeout_seconds: float
    buffer_size: int
    idle_timeout_seconds: float
    connect_fallback_on_main_connect_failure: bool
    tcp_keepalive: bool
    max_connections: int


@dataclass(frozen=True)
class LoggingConfig:
    level: str


@dataclass(frozen=True)
class MaintenanceConfig:
    mode: str
    force_fallback_file: Optional[str]
    force_main_file: Optional[str]


@dataclass(frozen=True)
class AppConfig:
    proxy: ProxyConfig
    main: TargetConfig
    fallback: TargetConfig
    healthcheck: HealthCheckConfig
    connection: ConnectionConfig
    logging: LoggingConfig
    maintenance: MaintenanceConfig


@dataclass(frozen=True)
class Target:
    name: str
    host: str
    port: int


@dataclass(frozen=True)
class TargetDecision:
    target: Target
    reason: str
    maintenance_mode: str


class HealthState:
    def __init__(self, fail_after: int, recover_after: int) -> None:
        self.fail_after = fail_after
        self.recover_after = recover_after
        self.main_healthy: bool = False
        self._successes: int = 0
        self._failures: int = 0

    def set_initial_state(self, ok: bool) -> None:
        self.main_healthy = ok
        self._successes = 1 if ok else 0
        self._failures = 0 if ok else 1

    def report(self, ok: bool) -> Optional[bool]:
        old_state = self.main_healthy
        if ok:
            self._successes += 1
            self._failures = 0
            if not self.main_healthy and self._successes >= self.recover_after:
                self.main_healthy = True
        else:
            self._failures += 1
            self._successes = 0
            if self.main_healthy and self._failures >= self.fail_after:
                self.main_healthy = False
        return self.main_healthy if old_state != self.main_healthy else None


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s | %(levelname)s | %(message)s",
        force=True,
    )


def _read_section(data: dict[str, Any], section: str) -> dict[str, Any]:
    value = data.get(section)
    if value is None:
        raise ConfigError(f"Fehlende Sektion: [{section}]")
    if not isinstance(value, dict):
        raise ConfigError(f"Sektion [{section}] muss ein TOML-Table sein.")
    return value


def _read_required(section_data: dict[str, Any], section: str, key: str) -> Any:
    if key not in section_data:
        raise ConfigError(f"Fehlender Konfigurationswert: [{section}].{key}")
    return section_data[key]


def _read_optional(section_data: dict[str, Any], key: str, default: Any = None) -> Any:
    return section_data.get(key, default)


def _clean_optional_string(value: Any) -> Any:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else None
    return value


def _clean_required_string(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise ConfigError(f"Konfigurationsdatei nicht gefunden: {path}")

    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Ungültiges TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Konfigurationsdatei konnte nicht gelesen werden: {path} ({exc})") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Konfigurationsdatei muss ein TOML-Objekt enthalten.")

    proxy = _read_section(raw, "proxy")
    main = _read_section(raw, "main")
    fallback = _read_section(raw, "fallback")
    healthcheck = _read_section(raw, "healthcheck")
    connection = _read_section(raw, "connection")
    logging_cfg = _read_section(raw, "logging")
    maintenance = raw.get("maintenance")
    if maintenance is None:
        maintenance = {}
    if not isinstance(maintenance, dict):
        raise ConfigError("Sektion [maintenance] muss ein TOML-Table sein.")

    config = AppConfig(
        proxy=ProxyConfig(
            listen_host=_clean_required_string(_read_required(proxy, "proxy", "listen_host")),
            listen_port=_read_required(proxy, "proxy", "listen_port"),
        ),
        main=TargetConfig(
            host=_clean_required_string(_read_required(main, "main", "host")),
            port=_read_required(main, "main", "port"),
        ),
        fallback=TargetConfig(
            host=_clean_required_string(_read_required(fallback, "fallback", "host")),
            port=_read_required(fallback, "fallback", "port"),
        ),
        healthcheck=HealthCheckConfig(
            mode=_read_required(healthcheck, "healthcheck", "mode"),
            interval_seconds=_read_required(healthcheck, "healthcheck", "interval_seconds"),
            timeout_seconds=_read_required(healthcheck, "healthcheck", "timeout_seconds"),
            fail_after=_read_required(healthcheck, "healthcheck", "fail_after"),
            recover_after=_read_required(healthcheck, "healthcheck", "recover_after"),
            target_host=_clean_optional_string(_read_optional(healthcheck, "target_host")),
            target_port=_read_optional(healthcheck, "target_port"),
            protocol_version=_read_optional(healthcheck, "protocol_version", 767),
            status_hostname=_clean_optional_string(_read_optional(healthcheck, "status_hostname")),
            require_valid_json=_read_optional(healthcheck, "require_valid_json", True),
            log_status_details=_read_optional(healthcheck, "log_status_details", False),
            jitter_seconds=_read_optional(healthcheck, "jitter_seconds", 0.0),
        ),
        connection=ConnectionConfig(
            timeout_seconds=_read_required(connection, "connection", "timeout_seconds"),
            buffer_size=_read_required(connection, "connection", "buffer_size"),
            idle_timeout_seconds=_read_optional(connection, "idle_timeout_seconds", 300.0),
            connect_fallback_on_main_connect_failure=_read_optional(connection, "connect_fallback_on_main_connect_failure", False),
            tcp_keepalive=_read_optional(connection, "tcp_keepalive", False),
            max_connections=_read_optional(connection, "max_connections", 4096),
        ),
        logging=LoggingConfig(level=_clean_required_string(_read_required(logging_cfg, "logging", "level"))),
        maintenance=MaintenanceConfig(
            mode=_clean_required_string(_read_optional(maintenance, "mode", "auto")),
            force_fallback_file=_clean_optional_string(_read_optional(maintenance, "force_fallback_file")),
            force_main_file=_clean_optional_string(_read_optional(maintenance, "force_main_file")),
        ),
    )
    validate_config(config)
    return config


def validate_config(config: AppConfig) -> None:
    def _is_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    def _validate_port(name: str, value: Any) -> None:
        if not _is_int(value) or not (1 <= value <= 65535):
            raise ConfigError(f"{name} muss ein Integer zwischen 1 und 65535 sein (aktuell: {value!r}).")

    _validate_port("proxy.listen_port", config.proxy.listen_port)
    _validate_port("main.port", config.main.port)
    _validate_port("fallback.port", config.fallback.port)

    for key, value in (
        ("healthcheck.fail_after", config.healthcheck.fail_after),
        ("healthcheck.recover_after", config.healthcheck.recover_after),
    ):
        if not _is_int(value) or value < 1:
            raise ConfigError(f"{key} muss ein Integer >= 1 sein (aktuell: {value!r}).")

    if not _is_int(config.connection.buffer_size) or config.connection.buffer_size < 1024:
        raise ConfigError(f"connection.buffer_size muss ein Integer >= 1024 sein (aktuell: {config.connection.buffer_size!r}).")

    if not _is_int(config.connection.max_connections) or config.connection.max_connections < 1:
        raise ConfigError("connection.max_connections muss ein Integer >= 1 sein.")

    if not isinstance(config.connection.connect_fallback_on_main_connect_failure, bool):
        raise ConfigError("connection.connect_fallback_on_main_connect_failure muss bool sein.")

    if not isinstance(config.connection.tcp_keepalive, bool):
        raise ConfigError("connection.tcp_keepalive muss bool sein.")

    if not isinstance(config.healthcheck.mode, str) or config.healthcheck.mode not in VALID_HEALTH_CHECK_MODES:
        raise ConfigError("healthcheck.mode muss 'tcp' oder 'minecraft_status' sein.")

    for host_name, host_value in (
        ("proxy.listen_host", config.proxy.listen_host),
        ("main.host", config.main.host),
        ("fallback.host", config.fallback.host),
    ):
        if not isinstance(host_value, str) or not host_value.strip():
            raise ConfigError(f"{host_name} muss ein nicht-leerer String sein (aktuell: {host_value!r}).")

    for key, value in (
        ("healthcheck.interval_seconds", config.healthcheck.interval_seconds),
        ("healthcheck.timeout_seconds", config.healthcheck.timeout_seconds),
        ("connection.timeout_seconds", config.connection.timeout_seconds),
        ("connection.idle_timeout_seconds", config.connection.idle_timeout_seconds),
    ):
        min_allowed = 0 if key == "connection.idle_timeout_seconds" else 0
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < min_allowed or (
            key != "connection.idle_timeout_seconds" and value <= 0
        ):
            raise ConfigError(f"{key} muss int oder float und > 0 sein (aktuell: {value!r}).")

    if isinstance(config.healthcheck.jitter_seconds, bool) or not isinstance(config.healthcheck.jitter_seconds, (int, float)) or config.healthcheck.jitter_seconds < 0:
        raise ConfigError(f"healthcheck.jitter_seconds muss int oder float und >= 0 sein (aktuell: {config.healthcheck.jitter_seconds!r}).")

    if not isinstance(config.logging.level, str) or config.logging.level.upper() not in VALID_LOG_LEVELS:
        raise ConfigError("logging.level muss DEBUG, INFO, WARNING, ERROR oder CRITICAL sein.")

    if config.healthcheck.target_host is not None:
        if not isinstance(config.healthcheck.target_host, str) or not config.healthcheck.target_host.strip():
            raise ConfigError("healthcheck.target_host muss ein nicht-leerer String sein, falls gesetzt.")

    if config.healthcheck.target_port is not None:
        _validate_port("healthcheck.target_port", config.healthcheck.target_port)

    if not _is_int(config.healthcheck.protocol_version) or config.healthcheck.protocol_version < 1:
        raise ConfigError("healthcheck.protocol_version muss ein Integer >= 1 sein.")

    if config.healthcheck.status_hostname is not None:
        if not isinstance(config.healthcheck.status_hostname, str) or not config.healthcheck.status_hostname.strip():
            raise ConfigError("healthcheck.status_hostname muss ein nicht-leerer String sein, falls gesetzt.")

    if not isinstance(config.healthcheck.require_valid_json, bool):
        raise ConfigError("healthcheck.require_valid_json muss bool sein.")

    if not isinstance(config.healthcheck.log_status_details, bool):
        raise ConfigError("healthcheck.log_status_details muss bool sein.")
    if not isinstance(config.maintenance.mode, str) or config.maintenance.mode not in {"auto", "force_fallback", "force_main"}:
        raise ConfigError("maintenance.mode muss 'auto', 'force_fallback' oder 'force_main' sein.")
    if config.maintenance.force_fallback_file is not None:
        if not isinstance(config.maintenance.force_fallback_file, str) or not config.maintenance.force_fallback_file.strip():
            raise ConfigError("maintenance.force_fallback_file muss ein nicht-leerer String sein, falls gesetzt.")
    if config.maintenance.force_main_file is not None:
        if not isinstance(config.maintenance.force_main_file, str) or not config.maintenance.force_main_file.strip():
            raise ConfigError("maintenance.force_main_file muss ein nicht-leerer String sein, falls gesetzt.")

    def _normalize_host(host: str) -> str:
        return host.strip().lower()

    loopback_hosts_v4 = {"127.0.0.1", "localhost"}
    loopback_or_any_v4 = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
    loopback_or_any_v6 = {"::1", "localhost", "::"}
    listen_host = _normalize_host(config.proxy.listen_host)

    def _validate_loop(name: str, target: TargetConfig, *, healthcheck_target: bool = False) -> None:
        normalized_target_host = _normalize_host(target.host)
        loop_text = "Healthcheck-Ziel erzeugt" if healthcheck_target else f"{name} erzeugt"
        if (normalized_target_host, target.port) == (listen_host, config.proxy.listen_port):
            raise ConfigError(f"{loop_text} exakt eine Proxy-Schleife zum Listener.")
        if target.port != config.proxy.listen_port:
            return
        if listen_host in loopback_hosts_v4 and normalized_target_host in loopback_hosts_v4:
            raise ConfigError(f"{loop_text} auf Loopback denselben Port wie der Listener.")
        if listen_host == "0.0.0.0" and normalized_target_host in loopback_or_any_v4:
            raise ConfigError(f"{loop_text} bei LISTEN_HOST=0.0.0.0 wahrscheinlich eine Proxy-Schleife.")
        if listen_host == "::" and normalized_target_host in loopback_or_any_v6:
            raise ConfigError(f"{loop_text} bei LISTEN_HOST=:: wahrscheinlich eine Proxy-Schleife.")

    _validate_loop("MAIN", config.main)
    _validate_loop("FALLBACK", config.fallback)
    _validate_loop("HEALTHCHECK", get_healthcheck_target(config), healthcheck_target=True)


def get_healthcheck_target(config: AppConfig) -> TargetConfig:
    host = config.healthcheck.target_host or config.main.host
    port = config.healthcheck.target_port or config.main.port
    return TargetConfig(host=host, port=port)


async def close_writer(writer: Optional[asyncio.StreamWriter]) -> None:
    if writer is None:
        return
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


def set_tcp_nodelay(writer: Optional[asyncio.StreamWriter]) -> None:
    if writer is None:
        return
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError as exc:
        log.debug("TCP_NODELAY konnte nicht gesetzt werden: %s", exc)




def set_tcp_keepalive(writer: Optional[asyncio.StreamWriter]) -> None:
    if writer is None:
        return
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError as exc:
        log.debug("TCP keepalive konnte nicht gesetzt werden: %s", exc)


async def tcp_health_check(host: str, port: int, timeout: float) -> HealthCheckResult:
    writer = None
    started = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        latency_ms = (time.monotonic() - started) * 1000.0
        return HealthCheckResult(ok=True, reason="tcp_connect_ok", latency_ms=latency_ms)
    except Exception as exc:
        log.debug("TCP-Healthcheck fehlgeschlagen für %s:%s: %s", host, port, exc, exc_info=True)
        return HealthCheckResult(ok=False, reason=f"tcp_connect_failed: {exc.__class__.__name__}")
    finally:
        await close_writer(writer)


def write_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("VarInt muss >= 0 sein")
    out = bytearray()
    while True:
        temp = value & 0x7F
        value >>= 7
        if value:
            temp |= 0x80
        out.append(temp)
        if not value:
            break
    return bytes(out)


async def read_varint(reader: asyncio.StreamReader) -> int:
    value = 0
    for position in range(MAX_VARINT_BYTES):
        current_byte = await reader.readexactly(1)
        byte_value = current_byte[0]
        value |= (byte_value & 0x7F) << (7 * position)
        if not byte_value & 0x80:
            return value
    raise ValueError("VarInt ist zu lang")


def make_minecraft_status_packet(host: str, port: int, protocol_version: int) -> bytes:
    server_host = host.encode("utf-8")
    packet_data = bytearray()
    packet_data += write_varint(0x00)
    packet_data += write_varint(protocol_version)
    packet_data += write_varint(len(server_host))
    packet_data += server_host
    packet_data += port.to_bytes(2, byteorder="big", signed=False)
    packet_data += write_varint(1)
    handshake_packet = write_varint(len(packet_data)) + packet_data
    request_data = write_varint(0x00)
    request_packet = write_varint(len(request_data)) + request_data
    return handshake_packet + request_packet


async def minecraft_status_health_check(
    host: str,
    port: int,
    timeout: float,
    protocol_version: int,
    status_hostname: Optional[str],
    require_valid_json: bool,
) -> HealthCheckResult:
    writer = None
    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        handshake_host = status_hostname or host
        writer.write(make_minecraft_status_packet(handshake_host, port, protocol_version))
        await asyncio.wait_for(writer.drain(), timeout=timeout)

        packet_length = await asyncio.wait_for(read_varint(reader), timeout=timeout)
        if packet_length < 1 or packet_length > MAX_PACKET_BYTES:
            return HealthCheckResult(ok=False, reason="invalid_packet_length")

        packet_payload = await asyncio.wait_for(reader.readexactly(packet_length), timeout=timeout)
        payload_reader = asyncio.StreamReader()
        payload_reader.feed_data(packet_payload)
        payload_reader.feed_eof()

        packet_id = await read_varint(payload_reader)
        if packet_id != 0x00:
            return HealthCheckResult(ok=False, reason=f"invalid_packet_id:{packet_id}")

        latency_ms = (time.monotonic() - started) * 1000.0
        if not require_valid_json:
            return HealthCheckResult(ok=True, reason="status_packet_ok", latency_ms=latency_ms)

        json_length = await read_varint(payload_reader)
        if json_length < 0 or json_length > MAX_STATUS_JSON_BYTES:
            return HealthCheckResult(ok=False, reason="status_json_too_large_or_negative")

        status_json = await payload_reader.readexactly(json_length)
        try:
            decoded_status = status_json.decode("utf-8")
        except UnicodeDecodeError:
            return HealthCheckResult(ok=False, reason="status_json_invalid_utf8")

        try:
            parsed = json.loads(decoded_status)
        except json.JSONDecodeError:
            return HealthCheckResult(ok=False, reason="status_json_invalid_json")
        if not isinstance(parsed, dict):
            return HealthCheckResult(ok=False, reason="status_json_not_object")

        version_name = None
        players_online = None
        players_max = None
        version_data = parsed.get("version")
        if isinstance(version_data, dict):
            name = version_data.get("name")
            if isinstance(name, str):
                version_name = name
        players_data = parsed.get("players")
        if isinstance(players_data, dict):
            online = players_data.get("online")
            maximum = players_data.get("max")
            if isinstance(online, int):
                players_online = online
            if isinstance(maximum, int):
                players_max = maximum

        return HealthCheckResult(
            ok=True,
            reason="status_json_ok",
            latency_ms=latency_ms,
            version_name=version_name,
            players_online=players_online,
            players_max=players_max,
        )
    except (asyncio.TimeoutError, ConnectionError, OSError, asyncio.IncompleteReadError, ValueError, json.JSONDecodeError) as exc:
        log.debug("Minecraft-Status-Healthcheck fehlgeschlagen für %s:%s: %s", host, port, exc, exc_info=True)
        return HealthCheckResult(ok=False, reason=f"status_check_failed: {exc.__class__.__name__}")
    finally:
        await close_writer(writer)


async def check_main_server(config: AppConfig) -> HealthCheckResult:
    target = get_healthcheck_target(config)
    if config.healthcheck.mode == "tcp":
        return await tcp_health_check(target.host, target.port, config.healthcheck.timeout_seconds)
    return await minecraft_status_health_check(
        target.host,
        target.port,
        config.healthcheck.timeout_seconds,
        config.healthcheck.protocol_version,
        config.healthcheck.status_hostname,
        config.healthcheck.require_valid_json,
    )


def choose_target(config: AppConfig, health: HealthState) -> Target:
    return choose_target_decision(config, health).target


def get_effective_maintenance_mode(config: AppConfig) -> tuple[str, str]:
    if config.maintenance.mode == "force_fallback":
        return "force_fallback", "config"
    if config.maintenance.mode == "force_main":
        return "force_main", "config"
    if config.maintenance.force_fallback_file and Path(config.maintenance.force_fallback_file).exists():
        return "force_fallback", "force_fallback_file"
    if config.maintenance.force_main_file and Path(config.maintenance.force_main_file).exists():
        return "force_main", "force_main_file"
    return "auto", "auto"


def choose_target_decision(config: AppConfig, health: HealthState) -> TargetDecision:
    mode, source = get_effective_maintenance_mode(config)
    if mode == "force_fallback":
        return TargetDecision(Target("FALLBACK", config.fallback.host, config.fallback.port), f"{mode}_{source}", mode)
    if mode == "force_main":
        return TargetDecision(Target("MAIN", config.main.host, config.main.port), f"{mode}_{source}", mode)
    current = config.main if health.main_healthy else config.fallback
    name = "MAIN" if health.main_healthy else "FALLBACK"
    reason = "health_main" if health.main_healthy else "health_fallback"
    return TargetDecision(Target(name=name, host=current.host, port=current.port), reason, mode)


async def pipe(source: asyncio.StreamReader, destination: asyncio.StreamWriter, direction_name: str, buffer_size: int, idle_timeout_seconds: float) -> None:
    try:
        while True:
            if idle_timeout_seconds > 0:
                data = await asyncio.wait_for(source.read(buffer_size), timeout=idle_timeout_seconds)
            else:
                data = await source.read(buffer_size)
            if not data:
                break
            destination.write(data)
            await destination.drain()
    except asyncio.TimeoutError:
        log.info("Verbindung wegen Idle-Timeout geschlossen (%s)", direction_name)
    except (ConnectionResetError, BrokenPipeError):
        log.debug("Verbindung zurückgesetzt (%s)", direction_name)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.debug("Pipe-Fehler %s: %s", direction_name, exc)


class ConnectionLimiter:
    def __init__(self, max_connections: int) -> None:
        self.max_connections = max_connections
        self._active = 0
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:
        async with self._lock:
            if self._active >= self.max_connections:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._active > 0:
                self._active -= 1


async def handle_client(config: AppConfig, health: HealthState, limiter: ConnectionLimiter, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    peer = client_writer.get_extra_info("peername")
    if not await limiter.try_acquire():
        log.warning("Verbindung von %s abgelehnt: max_connections erreicht", peer)
        await close_writer(client_writer)
        return

    decision = choose_target_decision(config, health)
    target = decision.target
    log.info("Neue Verbindung von %s -> %s %s:%s reason=%s", peer, target.name, target.host, target.port, decision.reason)
    server_writer = None
    try:
        try:
            server_reader, server_writer = await asyncio.wait_for(
                asyncio.open_connection(target.host, target.port), timeout=config.connection.timeout_seconds
            )
        except Exception as main_exc:
            if target.name == "MAIN" and config.connection.connect_fallback_on_main_connect_failure:
                fb = Target("FALLBACK", config.fallback.host, config.fallback.port)
                log.warning(
                    "MAIN connect fehlgeschlagen (%s:%s, %s), versuche FALLBACK sofort für %s -> %s:%s",
                    target.host,
                    target.port,
                    main_exc.__class__.__name__,
                    peer,
                    fb.host,
                    fb.port,
                )
                try:
                    server_reader, server_writer = await asyncio.wait_for(
                        asyncio.open_connection(fb.host, fb.port), timeout=config.connection.timeout_seconds
                    )
                    target = fb
                except Exception as fallback_exc:
                    log.error(
                        "MAIN connect fehlgeschlagen und FALLBACK connect ebenfalls fehlgeschlagen für %s (%s:%s, %s)",
                        peer,
                        fb.host,
                        fb.port,
                        fallback_exc.__class__.__name__,
                    )
                    raise
            else:
                raise
        set_tcp_nodelay(client_writer)
        set_tcp_nodelay(server_writer)
        if config.connection.tcp_keepalive:
            set_tcp_keepalive(client_writer)
            set_tcp_keepalive(server_writer)
        c2s = asyncio.create_task(pipe(client_reader, server_writer, "client -> server", config.connection.buffer_size, config.connection.idle_timeout_seconds))
        s2c = asyncio.create_task(pipe(server_reader, client_writer, "server -> client", config.connection.buffer_size, config.connection.idle_timeout_seconds))
        done, pending = await asyncio.wait({c2s, s2c}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*done, return_exceptions=True)
    except Exception as exc:
        log.error("Konnte nicht zu %s %s:%s verbinden: %s", target.name, target.host, target.port, exc)
    finally:
        await close_writer(server_writer)
        await close_writer(client_writer)
        await limiter.release()
        log.info("Verbindung beendet: %s", peer)


async def health_loop(config: AppConfig, health: HealthState, stop_event: asyncio.Event) -> None:
    target = get_healthcheck_target(config)
    log.info("Healthcheck gestartet (%s): %s:%s", config.healthcheck.mode, target.host, target.port)
    while not stop_event.is_set():
        try:
            result = await check_main_server(config)
            changed_to = health.report(result.ok)
            if config.healthcheck.log_status_details and result.ok:
                log.info(
                    "Healthcheck ok (%s): latency=%.1fms version=%s players=%s/%s reason=%s",
                    config.healthcheck.mode,
                    result.latency_ms if result.latency_ms is not None else -1,
                    result.version_name or "n/a",
                    result.players_online if result.players_online is not None else "n/a",
                    result.players_max if result.players_max is not None else "n/a",
                    result.reason,
                )
            elif not result.ok:
                log.debug("Healthcheck nicht ok: %s", result.reason)

            if changed_to is True:
                log.warning("FALLBACK -> MAIN (%s:%s): %s", target.host, target.port, result.reason)
            elif changed_to is False:
                log.error("MAIN -> FALLBACK (%s:%s): %s", target.host, target.port, result.reason)
        except Exception as exc:
            log.exception("Unerwarteter Fehler im Healthcheck-Loop: %s", exc)
        try:
            jitter = random.uniform(0, config.healthcheck.jitter_seconds) if config.healthcheck.jitter_seconds > 0 else 0.0
            await asyncio.wait_for(stop_event.wait(), timeout=config.healthcheck.interval_seconds + jitter)
        except asyncio.TimeoutError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minecraft TCP Failover Proxy")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Pfad zur TOML-Konfiguration")
    return parser.parse_args()


async def run() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 1

    setup_logging(config.logging.level)
    health = HealthState(config.healthcheck.fail_after, config.healthcheck.recover_after)

    stop_event = asyncio.Event()
    limiter = ConnectionLimiter(config.connection.max_connections)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            log.warning("Signal-Handler für %s auf dieser Plattform nicht unterstützt.", sig.name)

    initial_result = await check_main_server(config)
    health.set_initial_state(initial_result.ok)
    health_target = get_healthcheck_target(config)

    log.info(
        "Proxy-Start:\n  listen=%s:%s\n  route_main=%s:%s\n  route_fallback=%s:%s\n  healthcheck=%s %s:%s",
        config.proxy.listen_host,
        config.proxy.listen_port,
        config.main.host,
        config.main.port,
        config.fallback.host,
        config.fallback.port,
        config.healthcheck.mode,
        health_target.host,
        health_target.port,
    )
    if health.main_healthy:
        log.info("Startzustand: MAIN ist erreichbar (%s).", initial_result.reason)
    else:
        log.warning("Startzustand: MAIN ist nicht erreichbar (%s). Fallback aktiv.", initial_result.reason)

    try:
        server = await asyncio.start_server(
            lambda r, w: handle_client(config, health, limiter, r, w),
            config.proxy.listen_host,
            config.proxy.listen_port,
            start_serving=True,
        )
    except OSError as exc:
        log.error(
            "Konnte Listener nicht starten auf %s:%s: %s",
            config.proxy.listen_host,
            config.proxy.listen_port,
            exc,
        )
        return 1

    health_task = asyncio.create_task(health_loop(config, health, stop_event))
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    log.info("Proxy hört auf: %s", sockets)

    try:
        async with server:
            await stop_event.wait()
    finally:
        server.close()
        await server.wait_closed()
        health_task.cancel()
        await asyncio.gather(health_task, return_exceptions=True)
        log.info("Proxy sauber beendet.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
