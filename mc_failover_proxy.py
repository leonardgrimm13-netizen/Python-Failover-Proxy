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
from dataclasses import asdict, dataclass, is_dataclass
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
RECOVERY_PROGRESS_LOG_INTERVAL_SECONDS = 15.0
MAX_HTTP_REQUEST_LINE_BYTES = 4096
MAX_HTTP_HEADER_LINES = 64
MAX_HTTP_HEADER_BYTES = 16384
HTTP_READ_TIMEOUT_SECONDS = 2.0

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
    min_recovery_seconds: float
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
    monitoring: "MonitoringConfig"


@dataclass(frozen=True)
class MonitoringConfig:
    enabled: bool
    listen_host: str
    listen_port: int
    allow_remote: bool


@dataclass
class RuntimeState:
    started_at: float
    active_connections: int = 0
    total_connections: int = 0
    rejected_connections: int = 0
    active_target: str = "UNKNOWN"
    routing_reason: str = "startup"
    last_health_result: Optional[HealthCheckResult] = None
    last_health_check_at: Optional[float] = None


def update_runtime_routing_state(runtime_state: RuntimeState, target_name: str, reason: str) -> None:
    runtime_state.active_target = target_name
    runtime_state.routing_reason = reason


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
    def __init__(self, fail_after: int, recover_after: int, min_recovery_seconds: float = 0.0) -> None:
        self.fail_after = fail_after
        self.recover_after = recover_after
        self.min_recovery_seconds = float(min_recovery_seconds)
        self.main_healthy: bool = False
        self._successes: int = 0
        self._failures: int = 0
        self._recovery_started_at: Optional[float] = None

    @property
    def successes(self) -> int:
        return self._successes

    @property
    def failures(self) -> int:
        return self._failures

    def set_initial_state(self, ok: bool) -> None:
        self.main_healthy = ok
        self._successes = 1 if ok else 0
        self._failures = 0 if ok else 1
        self._recovery_started_at = None

    def recovery_remaining_seconds(self, now: Optional[float] = None) -> float:
        if self.main_healthy or self.min_recovery_seconds <= 0 or self._recovery_started_at is None:
            return 0.0
        current = time.monotonic() if now is None else now
        return max(0.0, self.min_recovery_seconds - (current - self._recovery_started_at))

    def recovery_elapsed_seconds(self, now: Optional[float] = None) -> float:
        if self.main_healthy or self.min_recovery_seconds <= 0 or self._recovery_started_at is None:
            return 0.0
        current = time.monotonic() if now is None else now
        return max(0.0, current - self._recovery_started_at)

    def is_recovering(self, now: Optional[float] = None) -> bool:
        return self.recovery_remaining_seconds(now) > 0

    def report(self, ok: bool, now: Optional[float] = None) -> Optional[bool]:
        current = time.monotonic() if now is None else now
        old_state = self.main_healthy
        if ok:
            self._failures = 0
            self._successes += 1
            if not self.main_healthy:
                if self._recovery_started_at is None:
                    self._recovery_started_at = current
                recovery_elapsed = current - self._recovery_started_at
                if self._successes >= self.recover_after and recovery_elapsed >= self.min_recovery_seconds:
                    self.main_healthy = True
                    self._recovery_started_at = None
        else:
            self._successes = 0
            self._recovery_started_at = None
            self._failures += 1
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
    monitoring = raw.get("monitoring")
    if monitoring is None:
        monitoring = {}
    if not isinstance(monitoring, dict):
        raise ConfigError("Sektion [monitoring] muss ein TOML-Table sein.")

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
            min_recovery_seconds=_read_optional(healthcheck, "min_recovery_seconds", 0.0),
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
        monitoring=MonitoringConfig(
            enabled=_read_optional(monitoring, "enabled", False),
            listen_host=_clean_required_string(_read_optional(monitoring, "listen_host", "127.0.0.1")),
            listen_port=_read_optional(monitoring, "listen_port", 8080),
            allow_remote=_read_optional(monitoring, "allow_remote", False),
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

    if isinstance(config.healthcheck.min_recovery_seconds, bool) or not isinstance(config.healthcheck.min_recovery_seconds, (int, float)) or config.healthcheck.min_recovery_seconds < 0:
        raise ConfigError(f"healthcheck.min_recovery_seconds muss int oder float und >= 0 sein (aktuell: {config.healthcheck.min_recovery_seconds!r}).")

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
    if not isinstance(config.monitoring.enabled, bool):
        raise ConfigError("monitoring.enabled muss bool sein.")
    if not isinstance(config.monitoring.listen_host, str) or not config.monitoring.listen_host.strip():
        raise ConfigError("monitoring.listen_host muss ein nicht-leerer String sein.")
    _validate_port("monitoring.listen_port", config.monitoring.listen_port)
    if not isinstance(config.monitoring.allow_remote, bool):
        raise ConfigError("monitoring.allow_remote muss bool sein.")
    local_monitor_hosts = {"127.0.0.1", "localhost", "::1"}
    monitor_host = _normalize_host(config.monitoring.listen_host)
    if config.monitoring.enabled and monitor_host not in local_monitor_hosts and not config.monitoring.allow_remote:
        raise ConfigError(
            "monitoring.listen_host ist nicht lokal. Setze monitoring.allow_remote=true, wenn ein Remote-Bind gewünscht ist."
        )


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
        reason = "force_fallback_config" if source == "config" else "force_fallback_file"
        return TargetDecision(Target("FALLBACK", config.fallback.host, config.fallback.port), reason, mode)
    if mode == "force_main":
        reason = "force_main_config" if source == "config" else "force_main_file"
        return TargetDecision(Target("MAIN", config.main.host, config.main.port), reason, mode)
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


async def handle_client(
    config: AppConfig,
    health: HealthState,
    limiter: ConnectionLimiter,
    runtime_state: RuntimeState,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    peer = client_writer.get_extra_info("peername")
    if not await limiter.try_acquire():
        log.warning("Verbindung von %s abgelehnt: max_connections erreicht", peer)
        runtime_state.rejected_connections += 1
        await close_writer(client_writer)
        return
    runtime_state.active_connections += 1
    runtime_state.total_connections += 1

    decision = choose_target_decision(config, health)
    target = decision.target
    update_runtime_routing_state(runtime_state, target.name, decision.reason)
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
                    update_runtime_routing_state(runtime_state, "FALLBACK", "main_connect_failed_fallback_connect")
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
        runtime_state.active_connections = max(0, runtime_state.active_connections - 1)
        log.info("Verbindung beendet: %s", peer)


def text_response(status: int, body: str, content_type: str) -> bytes:
    reason = {200: "OK", 404: "Not Found", 405: "Method Not Allowed", 400: "Bad Request", 503: "Service Unavailable"}.get(status, "OK")
    encoded = body.encode("utf-8")
    return (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(encoded)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("utf-8") + encoded


def json_response(status: int, payload: dict[str, Any]) -> bytes:
    return text_response(status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")


def build_state_payload(health: HealthState, runtime_state: RuntimeState) -> dict[str, Any]:
    uptime = max(0.0, time.monotonic() - runtime_state.started_at)
    return {
        "service": "mc-failover",
        "started_at": runtime_state.started_at,
        "uptime_seconds": uptime,
        "active_connections": runtime_state.active_connections,
        "total_connections": runtime_state.total_connections,
        "rejected_connections": runtime_state.rejected_connections,
        "active_target": runtime_state.active_target,
        "routing_reason": runtime_state.routing_reason,
        "main_healthy": health.main_healthy,
        "health_successes": health.successes,
        "health_failures": health.failures,
        "last_health_check_at": runtime_state.last_health_check_at,
        "last_health_result": asdict(runtime_state.last_health_result) if runtime_state.last_health_result else None,
    }


async def health_loop(config: AppConfig, health: HealthState, runtime_state: RuntimeState, stop_event: asyncio.Event) -> None:
    target = get_healthcheck_target(config)
    log.info("Healthcheck gestartet (%s): %s:%s", config.healthcheck.mode, target.host, target.port)
    last_recovery_log_at: Optional[float] = None
    was_recovering = False
    while not stop_event.is_set():
        try:
            result = await check_main_server(config)
            now = time.monotonic()
            runtime_state.last_health_result = result
            runtime_state.last_health_check_at = now
            changed_to = health.report(result.ok, now=now)
            if changed_to is not None:
                decision = choose_target_decision(config, health)
                update_runtime_routing_state(runtime_state, decision.target.name, decision.reason)
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

            if result.ok and health.is_recovering(now=now):
                elapsed = health.recovery_elapsed_seconds(now=now)
                should_log_progress = False
                if not was_recovering:
                    log.info(
                        "MAIN antwortet wieder, Recovery-Stabilisierung gestartet: %.1fs/%.1fs",
                        elapsed,
                        health.min_recovery_seconds,
                    )
                    last_recovery_log_at = now
                    was_recovering = True
                elif (
                    last_recovery_log_at is not None
                    and now - last_recovery_log_at >= RECOVERY_PROGRESS_LOG_INTERVAL_SECONDS
                ):
                    should_log_progress = True
                if should_log_progress:
                    log.info(
                        "MAIN weiterhin in Recovery-Stabilisierung: %.1fs/%.1fs, successful checks=%s/%s",
                        elapsed,
                        health.min_recovery_seconds,
                        health.successes,
                        health.recover_after,
                    )
                    last_recovery_log_at = now
            elif was_recovering and not result.ok:
                log.info("MAIN-Recovery wurde durch fehlgeschlagenen Healthcheck zurückgesetzt: %s", result.reason)
                was_recovering = False
                last_recovery_log_at = None
            elif was_recovering and not health.is_recovering(now=now):
                was_recovering = False
                last_recovery_log_at = None

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


async def handle_monitoring_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, config: AppConfig, health: HealthState, runtime_state: RuntimeState
) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=HTTP_READ_TIMEOUT_SECONDS)
        if not line or len(line) > MAX_HTTP_REQUEST_LINE_BYTES:
            writer.write(text_response(400, "bad request\n", "text/plain; charset=utf-8"))
            await writer.drain()
            return
        try:
            method, path, _version = line.decode("ascii", errors="replace").strip().split(" ", 2)
        except ValueError:
            writer.write(text_response(400, "bad request\n", "text/plain; charset=utf-8"))
            await writer.drain()
            return
        header_lines = 0
        header_bytes = 0
        while True:
            h = await asyncio.wait_for(reader.readline(), timeout=HTTP_READ_TIMEOUT_SECONDS)
            if not h or h in (b"\r\n", b"\n"):
                break
            header_lines += 1
            header_bytes += len(h)
            if header_lines > MAX_HTTP_HEADER_LINES or header_bytes > MAX_HTTP_HEADER_BYTES:
                writer.write(text_response(400, "bad request\n", "text/plain; charset=utf-8"))
                await writer.drain()
                return
        if method != "GET":
            writer.write(text_response(405, "method not allowed\n", "text/plain; charset=utf-8"))
            await writer.drain()
            return
        if path == "/health":
            payload = build_state_payload(health, runtime_state)
            lhr = runtime_state.last_health_result
            out = {
                "ok": True,
                "service": "mc-failover",
                "active_target": runtime_state.active_target,
                "main_healthy": health.main_healthy,
                "last_health_reason": lhr.reason if lhr else None,
                "last_health_latency_ms": lhr.latency_ms if lhr else None,
                "uptime_seconds": payload["uptime_seconds"],
            }
            writer.write(json_response(200, out))
        elif path == "/ready":
            writer.write(
                json_response(
                    200,
                    {
                        "ready": True,
                        "service": "mc-failover",
                        "active_target": runtime_state.active_target,
                        "main_healthy": health.main_healthy,
                    },
                )
            )
        elif path == "/state":
            writer.write(json_response(200, build_state_payload(health, runtime_state)))
        elif path == "/metrics":
            lhr = runtime_state.last_health_result
            latency = lhr.latency_ms if lhr and lhr.latency_ms is not None else -1
            uptime = max(0.0, time.monotonic() - runtime_state.started_at)
            body = "\n".join(
                [
                    "mc_failover_up 1",
                    f"mc_failover_uptime_seconds {uptime}",
                    f"mc_failover_active_connections {runtime_state.active_connections}",
                    f"mc_failover_total_connections {runtime_state.total_connections}",
                    f"mc_failover_rejected_connections {runtime_state.rejected_connections}",
                    f"mc_failover_main_healthy {1 if health.main_healthy else 0}",
                    f"mc_failover_last_health_latency_ms {latency}",
                    f'mc_failover_active_target{{target="MAIN"}} {1 if runtime_state.active_target == "MAIN" else 0}',
                    f'mc_failover_active_target{{target="FALLBACK"}} {1 if runtime_state.active_target == "FALLBACK" else 0}',
                    "",
                ]
            )
            writer.write(text_response(200, body, "text/plain; version=0.0.4; charset=utf-8"))
        else:
            writer.write(text_response(404, "not found\n", "text/plain; charset=utf-8"))
        await writer.drain()
    finally:
        await close_writer(writer)


async def start_monitoring_server(
    config: AppConfig, health: HealthState, runtime_state: RuntimeState, stop_event: asyncio.Event
) -> Optional[asyncio.AbstractServer]:
    if not config.monitoring.enabled:
        return None
    server = await asyncio.start_server(
        lambda r, w: handle_monitoring_client(r, w, config, health, runtime_state),
        config.monitoring.listen_host,
        config.monitoring.listen_port,
        start_serving=True,
    )
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    log.info("Monitoring hört auf: %s", sockets)
    return server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minecraft TCP Failover Proxy")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Pfad zur TOML-Konfiguration")
    parser.add_argument("--check-config", action="store_true", help="Konfiguration laden/validieren und beenden.")
    parser.add_argument("--test-main", action="store_true", help="TCP-Erreichbarkeit von [main] testen und beenden.")
    parser.add_argument("--test-fallback", action="store_true", help="TCP-Erreichbarkeit von [fallback] testen und beenden.")
    parser.add_argument("--test-healthcheck", action="store_true", help="Konfigurierten Healthcheck ausführen und beenden.")
    parser.add_argument("--print-effective-config", action="store_true", help="Effektive Konfiguration als JSON ausgeben und beenden.")
    return parser.parse_args()


def config_to_dict(config: AppConfig) -> dict[str, Any]:
    def _convert(value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if is_dataclass(value):
            return {k: _convert(v) for k, v in asdict(value).items()}
        if isinstance(value, dict):
            return {k: _convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_convert(v) for v in value]
        return value

    return _convert(config)


async def test_target_tcp(name: str, target: TargetConfig, timeout: float) -> HealthCheckResult:
    result = await tcp_health_check(target.host, target.port, timeout)
    if result.ok:
        latency = f"{result.latency_ms:.1f}" if result.latency_ms is not None else "n/a"
        print(f"OK: {name} erreichbar {target.host}:{target.port} latency={latency}ms")
    else:
        print(f"FEHLER: {name} nicht erreichbar {target.host}:{target.port} reason={result.reason}", file=sys.stderr)
    return result


async def run_cli_checks(args: argparse.Namespace, config: AppConfig) -> int:
    checks_requested = any(
        [args.check_config, args.print_effective_config, args.test_main, args.test_fallback, args.test_healthcheck]
    )
    if not checks_requested:
        return -1

    success = True
    if args.check_config:
        print(f"OK: Config ist gültig: {args.config}")

    if args.print_effective_config:
        print(json.dumps(config_to_dict(config), indent=2, sort_keys=True))

    if args.test_main:
        success = (await test_target_tcp("MAIN", config.main, config.connection.timeout_seconds)).ok and success

    if args.test_fallback:
        success = (await test_target_tcp("FALLBACK", config.fallback, config.connection.timeout_seconds)).ok and success

    if args.test_healthcheck:
        target = get_healthcheck_target(config)
        result = await check_main_server(config)
        if result.ok:
            latency = f"{result.latency_ms:.1f}" if result.latency_ms is not None else "n/a"
            details = (
                f" version={result.version_name or 'n/a'} players="
                f"{result.players_online if result.players_online is not None else 'n/a'}/"
                f"{result.players_max if result.players_max is not None else 'n/a'}"
            )
            print(
                f"OK: Healthcheck erfolgreich mode={config.healthcheck.mode} "
                f"target={target.host}:{target.port} latency={latency}ms{details}"
            )
        else:
            print(
                f"FEHLER: Healthcheck fehlgeschlagen mode={config.healthcheck.mode} "
                f"target={target.host}:{target.port} reason={result.reason}",
                file=sys.stderr,
            )
            success = False
    return 0 if success else 1


async def run() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 1

    cli_result = await run_cli_checks(args, config)
    if cli_result >= 0:
        return cli_result

    setup_logging(config.logging.level)
    health = HealthState(config.healthcheck.fail_after, config.healthcheck.recover_after, config.healthcheck.min_recovery_seconds)
    runtime_state = RuntimeState(started_at=time.monotonic())

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
    runtime_state.last_health_result = initial_result
    runtime_state.last_health_check_at = time.monotonic()
    initial_decision = choose_target_decision(config, health)
    update_runtime_routing_state(runtime_state, initial_decision.target.name, initial_decision.reason)
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
            lambda r, w: handle_client(config, health, limiter, runtime_state, r, w),
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

    try:
        monitoring_server = await start_monitoring_server(config, health, runtime_state, stop_event)
    except OSError as exc:
        log.error("Konnte Monitoring-Listener nicht starten auf %s:%s: %s", config.monitoring.listen_host, config.monitoring.listen_port, exc)
        server.close()
        await server.wait_closed()
        return 1

    health_task = asyncio.create_task(health_loop(config, health, runtime_state, stop_event))
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    log.info("Proxy hört auf: %s", sockets)

    try:
        async with server:
            await stop_event.wait()
    finally:
        server.close()
        await server.wait_closed()
        if monitoring_server is not None:
            monitoring_server.close()
            await monitoring_server.wait_closed()
        health_task.cancel()
        await asyncio.gather(health_task, return_exceptions=True)
        log.info("Proxy sauber beendet.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
