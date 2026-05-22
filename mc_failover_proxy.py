#!/usr/bin/env python3
import asyncio
import importlib
import logging
from pathlib import Path
import signal
import socket
import sys
from dataclasses import dataclass
from typing import Optional

# ============================================================
# EINSTELLUNGEN
# ============================================================

CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 25565

# Standard / Haupt-Minecraft-Server
MAIN_HOST = "127.0.0.1"
MAIN_PORT = 25567

# Fallback / Warteraum-Server
FALLBACK_HOST = "127.0.0.1"
FALLBACK_PORT = 25566

CHECK_INTERVAL_SECONDS = 3.0
CHECK_TIMEOUT_SECONDS = 2.0

# Wie oft der Check fehlschlagen muss, bevor auf Fallback geschaltet wird
FAIL_AFTER = 2

# Wie oft der Check erfolgreich sein muss, bevor zurück auf Main geschaltet wird
RECOVER_AFTER = 2

# "tcp" ist am stabilsten.
# Es prüft, ob der Port erreichbar ist.
HEALTH_CHECK_MODE = "tcp"

# Wenn du später ganz bewusst Minecraft-Status-Ping nutzen willst:
# HEALTH_CHECK_MODE = "minecraft_status"

BUFFER_SIZE = 64 * 1024
CONNECTION_TIMEOUT_SECONDS = 5.0
LOG_LEVEL = "INFO"


def _load_toml(path: Path) -> dict:
    module_name = "tomllib" if sys.version_info >= (3, 11) else "tomli"
    toml_module = importlib.import_module(module_name)
    with path.open("rb") as config_file:
        return toml_module.load(config_file)


def load_config(path: Path = CONFIG_PATH) -> None:
    global LISTEN_HOST, LISTEN_PORT
    global MAIN_HOST, MAIN_PORT
    global FALLBACK_HOST, FALLBACK_PORT
    global CHECK_INTERVAL_SECONDS, CHECK_TIMEOUT_SECONDS
    global FAIL_AFTER, RECOVER_AFTER
    global HEALTH_CHECK_MODE
    global BUFFER_SIZE, CONNECTION_TIMEOUT_SECONDS
    global LOG_LEVEL
    global MAIN_TARGET, FALLBACK_TARGET

    data = _load_toml(path)

    proxy = data["proxy"]
    main = data["main"]
    fallback = data["fallback"]
    healthcheck = data["healthcheck"]
    connection = data["connection"]
    logging_cfg = data["logging"]

    LISTEN_HOST = proxy["listen_host"]
    LISTEN_PORT = proxy["listen_port"]
    MAIN_HOST = main["host"]
    MAIN_PORT = main["port"]
    FALLBACK_HOST = fallback["host"]
    FALLBACK_PORT = fallback["port"]
    CHECK_INTERVAL_SECONDS = healthcheck["interval_seconds"]
    CHECK_TIMEOUT_SECONDS = healthcheck["timeout_seconds"]
    FAIL_AFTER = healthcheck["fail_after"]
    RECOVER_AFTER = healthcheck["recover_after"]
    HEALTH_CHECK_MODE = healthcheck["mode"]
    BUFFER_SIZE = connection["buffer_size"]
    CONNECTION_TIMEOUT_SECONDS = connection["timeout_seconds"]
    LOG_LEVEL = logging_cfg["level"]

    MAIN_TARGET = Target("MAIN", MAIN_HOST, MAIN_PORT)
    FALLBACK_TARGET = Target("FALLBACK", FALLBACK_HOST, FALLBACK_PORT)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("mc-failover")


@dataclass(frozen=True)
class Target:
    name: str
    host: str
    port: int


MAIN_TARGET = Target("MAIN", MAIN_HOST, MAIN_PORT)
FALLBACK_TARGET = Target("FALLBACK", FALLBACK_HOST, FALLBACK_PORT)


class HealthState:
    def __init__(self) -> None:
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
            if not self.main_healthy and self._successes >= RECOVER_AFTER:
                self.main_healthy = True
        else:
            self._failures += 1
            self._successes = 0
            if self.main_healthy and self._failures >= FAIL_AFTER:
                self.main_healthy = False

        if old_state != self.main_healthy:
            return self.main_healthy
        return None


health = HealthState()


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


async def tcp_health_check(host: str, port: int) -> bool:
    writer = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=CHECK_TIMEOUT_SECONDS
        )
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


async def minecraft_status_health_check(host: str, port: int) -> bool:
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=CHECK_TIMEOUT_SECONDS
        )

        writer.write(make_minecraft_status_packet(host, port))
        await asyncio.wait_for(writer.drain(), timeout=CHECK_TIMEOUT_SECONDS)

        _packet_length = await asyncio.wait_for(read_varint(reader), timeout=CHECK_TIMEOUT_SECONDS)
        packet_id = await asyncio.wait_for(read_varint(reader), timeout=CHECK_TIMEOUT_SECONDS)
        if packet_id != 0x00:
            log.debug("Unerwartete Packet-ID beim Status-Check: %s", packet_id)
            return False
        return True

    except Exception as exc:
        log.debug("Minecraft-Status-Healthcheck fehlgeschlagen für %s:%s: %s", host, port, exc)
        return False
    finally:
        await close_writer(writer)


async def check_main_server() -> bool:
    if HEALTH_CHECK_MODE == "tcp":
        return await tcp_health_check(MAIN_HOST, MAIN_PORT)
    if HEALTH_CHECK_MODE == "minecraft_status":
        return await minecraft_status_health_check(MAIN_HOST, MAIN_PORT)
    raise ValueError(f"Ungültiger HEALTH_CHECK_MODE: {HEALTH_CHECK_MODE}")


def validate_config() -> None:
    def _validate_port(name: str, value: int) -> None:
        if not isinstance(value, int) or not (1 <= value <= 65535):
            raise ValueError(
                f"{name} muss ein Integer zwischen 1 und 65535 sein (aktuell: {value!r})."
            )

    _validate_port("LISTEN_PORT", LISTEN_PORT)
    _validate_port("MAIN_PORT", MAIN_PORT)
    _validate_port("FALLBACK_PORT", FALLBACK_PORT)

    for key, value in (("FAIL_AFTER", FAIL_AFTER), ("RECOVER_AFTER", RECOVER_AFTER)):
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} muss ein Integer >= 1 sein (aktuell: {value!r}).")

    if not isinstance(BUFFER_SIZE, int) or BUFFER_SIZE <= 0:
        raise ValueError(f"BUFFER_SIZE muss ein Integer > 0 sein (aktuell: {BUFFER_SIZE!r}).")

    if HEALTH_CHECK_MODE not in {"tcp", "minecraft_status"}:
        raise ValueError("HEALTH_CHECK_MODE muss 'tcp' oder 'minecraft_status' sein.")

    for host_name, host_value in (
        ("LISTEN_HOST", LISTEN_HOST),
        ("MAIN_HOST", MAIN_HOST),
        ("FALLBACK_HOST", FALLBACK_HOST),
    ):
        if not isinstance(host_value, str) or not host_value.strip():
            raise ValueError(
                f"{host_name} muss ein nicht-leerer String sein (aktuell: {host_value!r})."
            )

    for key, value in (
        ("CHECK_INTERVAL_SECONDS", CHECK_INTERVAL_SECONDS),
        ("CHECK_TIMEOUT_SECONDS", CHECK_TIMEOUT_SECONDS),
        ("CONNECTION_TIMEOUT_SECONDS", CONNECTION_TIMEOUT_SECONDS),
    ):
        if not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"{key} muss int oder float und > 0 sein (aktuell: {value!r}).")

    valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if not isinstance(LOG_LEVEL, str) or LOG_LEVEL.upper() not in valid_log_levels:
        raise ValueError(
            "LOG_LEVEL muss ein gültiges Logging-Level sein (DEBUG, INFO, WARNING, ERROR, CRITICAL)."
        )

    def _normalize_host(host: str) -> str:
        return host.strip().lower()

    loopback_hosts_v4 = {"127.0.0.1", "localhost"}
    loopback_or_any_v4 = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
    loopback_or_any_v6 = {"::1", "localhost", "::"}

    listen_host = _normalize_host(LISTEN_HOST)

    def _validate_loop(name: str, target_host: str, target_port: int) -> None:
        normalized_target_host = _normalize_host(target_host)

        if (normalized_target_host, target_port) == (listen_host, LISTEN_PORT):
            raise ValueError(
                f"{name} zeigt exakt auf den Proxy-Listener ({LISTEN_HOST}:{LISTEN_PORT}). "
                "Das erzeugt eine Proxy-Schleife."
            )

        if target_port != LISTEN_PORT:
            return

        if listen_host in loopback_hosts_v4 and normalized_target_host in loopback_hosts_v4:
            raise ValueError(
                f"{name} nutzt denselben Port wie der Listener und zeigt auf Loopback "
                f"({target_host}:{target_port}). Das erzeugt wahrscheinlich eine Proxy-Schleife."
            )

        if listen_host == "0.0.0.0" and normalized_target_host in loopback_or_any_v4:
            raise ValueError(
                f"{name} nutzt denselben Port wie LISTEN ({LISTEN_PORT}) bei LISTEN_HOST=0.0.0.0 "
                f"und Zielhost={target_host}. Das erzeugt wahrscheinlich eine Proxy-Schleife."
            )

        if listen_host == "::" and normalized_target_host in loopback_or_any_v6:
            raise ValueError(
                f"{name} nutzt denselben Port wie LISTEN ({LISTEN_PORT}) bei LISTEN_HOST=:: "
                f"und Zielhost={target_host}. Das erzeugt wahrscheinlich eine Proxy-Schleife."
            )

    _validate_loop("MAIN", MAIN_HOST, MAIN_PORT)
    _validate_loop("FALLBACK", FALLBACK_HOST, FALLBACK_PORT)


async def health_loop(stop_event: asyncio.Event) -> None:
    log.info("Healthcheck gestartet (%s): %s:%s", HEALTH_CHECK_MODE, MAIN_HOST, MAIN_PORT)
    try:
        while not stop_event.is_set():
            try:
                ok = await check_main_server()
                changed_to = health.report(ok)

                if changed_to is True:
                    log.warning("Hauptserver wieder erreichbar. Neue Spieler gehen auf MAIN.")
                elif changed_to is False:
                    log.error("Hauptserver nicht erreichbar. Neue Spieler gehen auf FALLBACK.")
            except Exception as exc:
                log.exception("Unerwarteter Fehler im Healthcheck-Loop: %s", exc)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=CHECK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError:
        log.debug("Healthcheck-Loop wurde abgebrochen.")
        raise


def choose_target() -> Target:
    return MAIN_TARGET if health.main_healthy else FALLBACK_TARGET


async def pipe(
    source: asyncio.StreamReader, destination: asyncio.StreamWriter, direction_name: str
) -> None:
    try:
        while True:
            data = await source.read(BUFFER_SIZE)
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


async def handle_client(
    client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
) -> None:
    peer = client_writer.get_extra_info("peername")
    target = choose_target()

    log.info("Neue Verbindung von %s -> %s %s:%s", peer, target.name, target.host, target.port)

    server_writer = None
    try:
        server_reader, server_writer = await asyncio.wait_for(
            asyncio.open_connection(target.host, target.port), timeout=CONNECTION_TIMEOUT_SECONDS
        )
        set_tcp_nodelay(client_writer)
        set_tcp_nodelay(server_writer)

        task_client_to_server = asyncio.create_task(
            pipe(client_reader, server_writer, "client -> server")
        )
        task_server_to_client = asyncio.create_task(
            pipe(server_reader, client_writer, "server -> client")
        )

        done, pending = await asyncio.wait(
            {task_client_to_server, task_server_to_client}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()

        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*done, return_exceptions=True)

    except Exception as exc:
        log.error(
            "Konnte nicht zu %s %s:%s verbinden: %s", target.name, target.host, target.port, exc
        )
    finally:
        await close_writer(server_writer)
        await close_writer(client_writer)
        log.info("Verbindung beendet: %s", peer)


async def main() -> None:
    try:
        load_config()
        validate_config()
    except (FileNotFoundError, KeyError, ValueError, ModuleNotFoundError) as exc:
        log.error("Ungültige Konfiguration: %s", exc)
        return
    log.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            log.warning("Signal-Handler für %s auf dieser Plattform nicht unterstützt.", sig.name)

    initial_ok = await check_main_server()
    health.set_initial_state(initial_ok)

    log.info(
        "Proxy-Start: listen=%s:%s, main=%s:%s, fallback=%s:%s, mode=%s",
        LISTEN_HOST,
        LISTEN_PORT,
        MAIN_HOST,
        MAIN_PORT,
        FALLBACK_HOST,
        FALLBACK_PORT,
        HEALTH_CHECK_MODE,
    )
    if health.main_healthy:
        log.info("Startzustand: MAIN ist erreichbar.")
    else:
        log.warning("Startzustand: MAIN ist nicht erreichbar. Fallback aktiv.")

    server = await asyncio.start_server(handle_client, LISTEN_HOST, LISTEN_PORT, start_serving=True)
    health_task = asyncio.create_task(health_loop(stop_event))

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


if __name__ == "__main__":
    asyncio.run(main())
