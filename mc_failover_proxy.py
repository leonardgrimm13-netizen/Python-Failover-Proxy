#!/usr/bin/env python3
import argparse
import asyncio
import logging
import signal
import socket
import sys
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


@dataclass(frozen=True)
class ConnectionConfig:
    timeout_seconds: float
    buffer_size: int


@dataclass(frozen=True)
class LoggingConfig:
    level: str


@dataclass(frozen=True)
class AppConfig:
    proxy: ProxyConfig
    main: TargetConfig
    fallback: TargetConfig
    healthcheck: HealthCheckConfig
    connection: ConnectionConfig
    logging: LoggingConfig


@dataclass(frozen=True)
class Target:
    name: str
    host: str
    port: int


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

    config = AppConfig(
        proxy=ProxyConfig(
            listen_host=_read_required(proxy, "proxy", "listen_host"),
            listen_port=_read_required(proxy, "proxy", "listen_port"),
        ),
        main=TargetConfig(
            host=_read_required(main, "main", "host"),
            port=_read_required(main, "main", "port"),
        ),
        fallback=TargetConfig(
            host=_read_required(fallback, "fallback", "host"),
            port=_read_required(fallback, "fallback", "port"),
        ),
        healthcheck=HealthCheckConfig(
            mode=_read_required(healthcheck, "healthcheck", "mode"),
            interval_seconds=_read_required(healthcheck, "healthcheck", "interval_seconds"),
            timeout_seconds=_read_required(healthcheck, "healthcheck", "timeout_seconds"),
            fail_after=_read_required(healthcheck, "healthcheck", "fail_after"),
            recover_after=_read_required(healthcheck, "healthcheck", "recover_after"),
        ),
        connection=ConnectionConfig(
            timeout_seconds=_read_required(connection, "connection", "timeout_seconds"),
            buffer_size=_read_required(connection, "connection", "buffer_size"),
        ),
        logging=LoggingConfig(level=_read_required(logging_cfg, "logging", "level")),
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

    if not _is_int(config.connection.buffer_size) or config.connection.buffer_size <= 0:
        raise ConfigError(f"connection.buffer_size muss ein Integer > 0 sein (aktuell: {config.connection.buffer_size!r}).")

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
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ConfigError(f"{key} muss int oder float und > 0 sein (aktuell: {value!r}).")

    if not isinstance(config.logging.level, str) or config.logging.level.upper() not in VALID_LOG_LEVELS:
        raise ConfigError("logging.level muss DEBUG, INFO, WARNING, ERROR oder CRITICAL sein.")

    def _normalize_host(host: str) -> str:
        return host.strip().lower()

    loopback_hosts_v4 = {"127.0.0.1", "localhost"}
    loopback_or_any_v4 = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
    loopback_or_any_v6 = {"::1", "localhost", "::"}
    listen_host = _normalize_host(config.proxy.listen_host)

    def _validate_loop(name: str, target: TargetConfig) -> None:
        normalized_target_host = _normalize_host(target.host)
        if (normalized_target_host, target.port) == (listen_host, config.proxy.listen_port):
            raise ConfigError(f"{name} zeigt exakt auf den Proxy-Listener und erzeugt eine Proxy-Schleife.")
        if target.port != config.proxy.listen_port:
            return
        if listen_host in loopback_hosts_v4 and normalized_target_host in loopback_hosts_v4:
            raise ConfigError(f"{name} nutzt denselben Port wie der Listener auf Loopback. Proxy-Schleife.")
        if listen_host == "0.0.0.0" and normalized_target_host in loopback_or_any_v4:
            raise ConfigError(f"{name} erzeugt bei LISTEN_HOST=0.0.0.0 wahrscheinlich eine Proxy-Schleife.")
        if listen_host == "::" and normalized_target_host in loopback_or_any_v6:
            raise ConfigError(f"{name} erzeugt bei LISTEN_HOST=:: wahrscheinlich eine Proxy-Schleife.")

    _validate_loop("MAIN", config.main)
    _validate_loop("FALLBACK", config.fallback)


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


async def tcp_health_check(host: str, port: int, timeout: float) -> bool:
    writer = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        return True
    except Exception as exc:
        log.debug("TCP-Healthcheck fehlgeschlagen für %s:%s: %s", host, port, exc)
        return False
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
    for position in range(5):
        current_byte = await reader.readexactly(1)
        byte_value = current_byte[0]
        value |= (byte_value & 0x7F) << (7 * position)
        if not byte_value & 0x80:
            return value
    raise ValueError("VarInt ist zu lang")


def make_minecraft_status_packet(host: str, port: int) -> bytes:
    protocol_version = 47
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


async def minecraft_status_health_check(host: str, port: int, timeout: float) -> bool:
    writer = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.write(make_minecraft_status_packet(host, port))
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        _packet_length = await asyncio.wait_for(read_varint(reader), timeout=timeout)
        packet_id = await asyncio.wait_for(read_varint(reader), timeout=timeout)
        return packet_id == 0x00
    except Exception as exc:
        log.debug("Minecraft-Status-Healthcheck fehlgeschlagen für %s:%s: %s", host, port, exc)
        return False
    finally:
        await close_writer(writer)


async def check_main_server(config: AppConfig) -> bool:
    if config.healthcheck.mode == "tcp":
        return await tcp_health_check(config.main.host, config.main.port, config.healthcheck.timeout_seconds)
    return await minecraft_status_health_check(config.main.host, config.main.port, config.healthcheck.timeout_seconds)


def choose_target(config: AppConfig, health: HealthState) -> Target:
    current = config.main if health.main_healthy else config.fallback
    name = "MAIN" if health.main_healthy else "FALLBACK"
    return Target(name=name, host=current.host, port=current.port)


async def pipe(source: asyncio.StreamReader, destination: asyncio.StreamWriter, direction_name: str, buffer_size: int) -> None:
    try:
        while True:
            data = await source.read(buffer_size)
            if not data:
                break
            destination.write(data)
            await destination.drain()
    except (ConnectionResetError, BrokenPipeError):
        log.debug("Verbindung zurückgesetzt (%s)", direction_name)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.debug("Pipe-Fehler %s: %s", direction_name, exc)


async def handle_client(config: AppConfig, health: HealthState, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    peer = client_writer.get_extra_info("peername")
    target = choose_target(config, health)
    log.info("Neue Verbindung von %s -> %s %s:%s", peer, target.name, target.host, target.port)
    server_writer = None
    try:
        server_reader, server_writer = await asyncio.wait_for(
            asyncio.open_connection(target.host, target.port), timeout=config.connection.timeout_seconds
        )
        set_tcp_nodelay(client_writer)
        set_tcp_nodelay(server_writer)
        c2s = asyncio.create_task(pipe(client_reader, server_writer, "client -> server", config.connection.buffer_size))
        s2c = asyncio.create_task(pipe(server_reader, client_writer, "server -> client", config.connection.buffer_size))
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
        log.info("Verbindung beendet: %s", peer)


async def health_loop(config: AppConfig, health: HealthState, stop_event: asyncio.Event) -> None:
    log.info("Healthcheck gestartet (%s): %s:%s", config.healthcheck.mode, config.main.host, config.main.port)
    while not stop_event.is_set():
        try:
            ok = await check_main_server(config)
            changed_to = health.report(ok)
            if changed_to is True:
                log.warning("Hauptserver wieder erreichbar. Neue Spieler gehen auf MAIN.")
            elif changed_to is False:
                log.error("Hauptserver nicht erreichbar. Neue Spieler gehen auf FALLBACK.")
        except Exception as exc:
            log.exception("Unerwarteter Fehler im Healthcheck-Loop: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=config.healthcheck.interval_seconds)
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
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            log.warning("Signal-Handler für %s auf dieser Plattform nicht unterstützt.", sig.name)

    initial_ok = await check_main_server(config)
    health.set_initial_state(initial_ok)

    log.info(
        "Proxy-Start: listen=%s:%s, main=%s:%s, fallback=%s:%s, mode=%s",
        config.proxy.listen_host,
        config.proxy.listen_port,
        config.main.host,
        config.main.port,
        config.fallback.host,
        config.fallback.port,
        config.healthcheck.mode,
    )
    if health.main_healthy:
        log.info("Startzustand: MAIN ist erreichbar.")
    else:
        log.warning("Startzustand: MAIN ist nicht erreichbar. Fallback aktiv.")

    try:
        server = await asyncio.start_server(
            lambda r, w: handle_client(config, health, r, w),
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
