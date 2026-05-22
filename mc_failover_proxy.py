#!/usr/bin/env python3
import asyncio
import logging
import signal
import socket
from dataclasses import dataclass
from typing import Optional


# ============================================================
# EINSTELLUNGEN
# ============================================================

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


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
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

    def report(self, ok: bool) -> Optional[bool]:
        """
        Gibt True/False zurück, wenn sich der Zustand geändert hat.
        Gibt None zurück, wenn der Zustand gleich bleibt.
        """
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


async def tcp_health_check(host: str, port: int) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=CHECK_TIMEOUT_SECONDS,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


def write_varint(value: int) -> bytes:
    value &= 0xFFFFFFFF
    out = bytearray()

    while True:
        temp = value & 0b01111111
        value >>= 7

        if value != 0:
            temp |= 0b10000000

        out.append(temp)

        if value == 0:
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
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=CHECK_TIMEOUT_SECONDS,
        )

        packet = make_minecraft_status_packet(host, port)
        writer.write(packet)
        await writer.drain()

        await asyncio.wait_for(read_varint(reader), timeout=CHECK_TIMEOUT_SECONDS)
        packet_id = await asyncio.wait_for(read_varint(reader), timeout=CHECK_TIMEOUT_SECONDS)

        writer.close()
        await writer.wait_closed()

        return packet_id == 0x00

    except Exception:
        return False


async def check_main_server() -> bool:
    if HEALTH_CHECK_MODE == "minecraft_status":
        return await minecraft_status_health_check(MAIN_HOST, MAIN_PORT)

    return await tcp_health_check(MAIN_HOST, MAIN_PORT)


async def health_loop(stop_event: asyncio.Event) -> None:
    log.info("Healthcheck gestartet: %s:%s", MAIN_HOST, MAIN_PORT)

    while not stop_event.is_set():
        ok = await check_main_server()
        changed_to = health.report(ok)

        if changed_to is True:
            log.warning("Hauptserver ist wieder erreichbar. Neue Spieler gehen wieder auf MAIN.")
        elif changed_to is False:
            log.error("Hauptserver ist nicht erreichbar. Neue Spieler gehen auf FALLBACK.")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


def choose_target() -> Target:
    if health.main_healthy:
        return MAIN_TARGET

    return FALLBACK_TARGET


async def pipe(
    source: asyncio.StreamReader,
    destination: asyncio.StreamWriter,
    direction_name: str,
) -> None:
    try:
        while True:
            data = await source.read(BUFFER_SIZE)

            if not data:
                break

            destination.write(data)
            await destination.drain()

    except ConnectionResetError:
        pass
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.debug("Pipe-Fehler %s: %s", direction_name, exc)
    finally:
        try:
            destination.close()
            await destination.wait_closed()
        except Exception:
            pass


async def handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    peer = client_writer.get_extra_info("peername")
    target = choose_target()

    log.info(
        "Neue Verbindung von %s -> %s %s:%s",
        peer,
        target.name,
        target.host,
        target.port,
    )

    try:
        server_reader, server_writer = await asyncio.wait_for(
            asyncio.open_connection(target.host, target.port),
            timeout=CONNECTION_TIMEOUT_SECONDS,
        )

        client_socket = client_writer.get_extra_info("socket")
        server_socket = server_writer.get_extra_info("socket")

        if client_socket is not None:
            client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        if server_socket is not None:
            server_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    except Exception as exc:
        log.error(
            "Konnte nicht zu %s %s:%s verbinden: %s",
            target.name,
            target.host,
            target.port,
            exc,
        )

        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass

        return

    task_client_to_server = asyncio.create_task(
        pipe(client_reader, server_writer, "client -> server")
    )

    task_server_to_client = asyncio.create_task(
        pipe(server_reader, client_writer, "server -> client")
    )

    done, pending = await asyncio.wait(
        {task_client_to_server, task_server_to_client},
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()

    await asyncio.gather(*pending, return_exceptions=True)
    await asyncio.gather(*done, return_exceptions=True)

    log.info("Verbindung beendet: %s", peer)


async def main() -> None:
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    initial_ok = await check_main_server()
    health.report(initial_ok)

    if health.main_healthy:
        log.info("Startzustand: MAIN ist erreichbar.")
    else:
        log.warning("Startzustand: MAIN ist nicht erreichbar. Fallback aktiv.")

    server = await asyncio.start_server(
        handle_client,
        LISTEN_HOST,
        LISTEN_PORT,
        start_serving=True,
    )

    health_task = asyncio.create_task(health_loop(stop_event))

    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    log.info("Proxy hört auf: %s", sockets)
    log.info("MAIN: %s:%s", MAIN_HOST, MAIN_PORT)
    log.info("FALLBACK: %s:%s", FALLBACK_HOST, FALLBACK_PORT)

    async with server:
        await stop_event.wait()

    server.close()
    await server.wait_closed()

    health_task.cancel()
    await asyncio.gather(health_task, return_exceptions=True)

    log.info("Proxy sauber beendet.")


if __name__ == "__main__":
    asyncio.run(main())
