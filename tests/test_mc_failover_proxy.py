import asyncio
import unittest
from pathlib import Path

import mc_failover_proxy as m


BASE = """[proxy]
listen_host = "0.0.0.0"
listen_port = 25565

[main]
host = "127.0.0.1"
port = 25567

[fallback]
host = "127.0.0.1"
port = 25566

[healthcheck]
mode = "tcp"
interval_seconds = 3.0
timeout_seconds = 2.0
fail_after = 2
recover_after = 2

[connection]
timeout_seconds = 5.0
buffer_size = 65536

[logging]
level = "INFO"
"""


class DummyReader:
    def __init__(self, data: bytes):
        self._data = bytearray(data)

    async def readexactly(self, n: int) -> bytes:
        if len(self._data) < n:
            raise asyncio.IncompleteReadError(partial=bytes(self._data), expected=n)
        out = bytes(self._data[:n])
        del self._data[:n]
        return out


class ConfigAndCoreTests(unittest.TestCase):
    def load_from_text(self, text: str) -> m.AppConfig:
        p = Path("/tmp/test_mc_failover_proxy_config.toml")
        p.write_text(text, encoding="utf-8")
        self.addCleanup(lambda: p.exists() and p.unlink())
        return m.load_config(p)

    def test_old_config_compatibility(self):
        cfg = self.load_from_text(BASE)
        self.assertIsNone(cfg.healthcheck.target_host)
        self.assertIsNone(cfg.healthcheck.target_port)
        self.assertEqual(cfg.healthcheck.protocol_version, 767)
        self.assertIsNone(cfg.healthcheck.status_hostname)
        self.assertTrue(cfg.healthcheck.require_valid_json)
        self.assertFalse(cfg.healthcheck.log_status_details)

    def test_new_config_parsing(self):
        text = BASE.replace('mode = "tcp"', 'mode = "minecraft_status"')
        text = text.replace('recover_after = 2', 'recover_after = 2\ntarget_host = "100.64.0.10"\ntarget_port = 25568\nprotocol_version = 768\nstatus_hostname = "survival.example.com"\nrequire_valid_json = true\nlog_status_details = true')
        cfg = self.load_from_text(text)
        self.assertEqual(cfg.healthcheck.target_host, "100.64.0.10")
        self.assertEqual(cfg.healthcheck.target_port, 25568)
        self.assertEqual(cfg.healthcheck.protocol_version, 768)
        self.assertEqual(cfg.healthcheck.status_hostname, "survival.example.com")
        self.assertTrue(cfg.healthcheck.require_valid_json)
        self.assertTrue(cfg.healthcheck.log_status_details)

    def test_validation_cases(self):
        bad = [
            'target_port = "abc"', 'target_port = 0', 'target_port = 65536', 'target_host = " "',
            'status_hostname = " "', 'protocol_version = 0', 'require_valid_json = "yes"', 'log_status_details = "yes"'
        ]
        for line in bad:
            text = BASE.replace("recover_after = 2", f"recover_after = 2\n{line}")
            with self.assertRaises(m.ConfigError, msg=line):
                self.load_from_text(text)


class VarIntAndTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_varint_roundtrip(self):
        for value in (0, 1, 127, 128, 255, 2097151):
            self.assertEqual(value, await m.read_varint(DummyReader(m.write_varint(value))))

    async def test_get_healthcheck_target_overrides(self):
        hc = m.HealthCheckConfig("tcp", 3, 2, 2, 2, None, None, 767, None, True, False)
        cfg = m.AppConfig(m.ProxyConfig("0.0.0.0", 25565), m.TargetConfig("a", 1), m.TargetConfig("b", 2), hc, m.ConnectionConfig(1, 1), m.LoggingConfig("INFO"))
        self.assertEqual(m.get_healthcheck_target(cfg), m.TargetConfig("a", 1))
        cfg = m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", 3, 2, 2, 2, "x", None, 767, None, True, False)})
        self.assertEqual(m.get_healthcheck_target(cfg), m.TargetConfig("x", 1))
        cfg = m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", 3, 2, 2, 2, None, 9, 767, None, True, False)})
        self.assertEqual(m.get_healthcheck_target(cfg), m.TargetConfig("a", 9))

    async def test_packet_creation(self):
        packet = m.make_minecraft_status_packet("survival.example.com", 25567, 767)
        self.assertIsInstance(packet, bytes)
        self.assertIn(b"survival.example.com", packet)
        self.assertIn(m.write_varint(767), packet)
        self.assertIn(b"\x01", packet)

    async def test_minecraft_status_healthcheck_paths(self):
        async def server_valid(reader, writer):
            await reader.read(2048)
            status = b'{"version":{"name":"1.21"},"players":{"online":1,"max":20}}'
            payload = m.write_varint(0) + m.write_varint(len(status)) + status
            writer.write(m.write_varint(len(payload)) + payload)
            await writer.drain(); await m.close_writer(writer)

        async def server_bad_id(reader, writer):
            await reader.read(2048)
            payload = m.write_varint(1) + m.write_varint(0)
            writer.write(m.write_varint(len(payload)) + payload)
            await writer.drain(); await m.close_writer(writer)

        async def server_bad_json(reader, writer):
            await reader.read(2048)
            bad = b"{notjson"
            payload = m.write_varint(0) + m.write_varint(len(bad)) + bad
            writer.write(m.write_varint(len(payload)) + payload)
            await writer.drain(); await m.close_writer(writer)

        async def run_once(handler):
            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            return server, port

        server, port = await run_once(server_valid)
        try:
            r = await m.minecraft_status_health_check("127.0.0.1", port, 1.0, 767, None, True)
            self.assertTrue(r.ok)
        finally:
            server.close(); await server.wait_closed()

        server, port = await run_once(server_bad_id)
        try:
            r = await m.minecraft_status_health_check("127.0.0.1", port, 1.0, 767, None, True)
            self.assertFalse(r.ok)
        finally:
            server.close(); await server.wait_closed()

        server, port = await run_once(server_bad_json)
        try:
            self.assertFalse((await m.minecraft_status_health_check("127.0.0.1", port, 1.0, 767, None, True)).ok)
            self.assertTrue((await m.minecraft_status_health_check("127.0.0.1", port, 1.0, 767, None, False)).ok)
        finally:
            server.close(); await server.wait_closed()

        self.assertFalse((await m.minecraft_status_health_check("127.0.0.1", 9, 0.01, 767, None, True)).ok)


if __name__ == "__main__":
    unittest.main()
