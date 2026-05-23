import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mc_failover_proxy as m

REPO_ROOT = Path(__file__).resolve().parents[1]

VALID_CONFIG_TOML = """[proxy]
listen_host = "0.0.0.0"
listen_port = 25565

[main]
host = "127.0.0.1"
port = 25564

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


class ConfigTests(unittest.TestCase):
    def write_temp_config(self, text: str = VALID_CONFIG_TOML) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "config.toml"
        p.write_text(text, encoding="utf-8")
        return p

    def valid_config(self) -> m.AppConfig:
        return m.AppConfig(
            proxy=m.ProxyConfig("0.0.0.0", 25565),
            main=m.TargetConfig("127.0.0.1", 25564),
            fallback=m.TargetConfig("127.0.0.1", 25566),
            healthcheck=m.HealthCheckConfig("tcp", 3.0, 2.0, 2, 2, None, None, 767, None, True, False, 0.0),
            connection=m.ConnectionConfig(5.0, 65536, 300.0, False, False, 4096),
            logging=m.LoggingConfig("INFO"),
        )

    def test_repo_config_toml_is_valid(self):
        cfg = m.load_config(REPO_ROOT / "config.toml")
        self.assertEqual(cfg.logging.level, "INFO")

    def test_config_example_toml_is_valid(self):
        cfg = m.load_config(REPO_ROOT / "config.example.toml")
        self.assertEqual(cfg.proxy.listen_port, 25565)
        self.assertEqual(cfg.main.port, 25564)
        self.assertEqual(cfg.fallback.port, 25566)
        self.assertEqual(cfg.healthcheck.mode, "tcp")
        self.assertIsNone(cfg.healthcheck.target_host)
        self.assertIsNone(cfg.healthcheck.target_port)


    def test_required_host_strings_are_stripped(self):
        text = VALID_CONFIG_TOML.replace('listen_host = "0.0.0.0"', 'listen_host = " 0.0.0.0 "').replace(
            'host = "127.0.0.1"\nport = 25564', 'host = " 127.0.0.1 "\nport = 25564'
        ).replace('host = "127.0.0.1"\nport = 25566', 'host = " 127.0.0.1 "\nport = 25566')
        cfg = m.load_config(self.write_temp_config(text))
        self.assertEqual(cfg.proxy.listen_host, "0.0.0.0")
        self.assertEqual(cfg.main.host, "127.0.0.1")
        self.assertEqual(cfg.fallback.host, "127.0.0.1")

    def test_optional_healthcheck_strings_are_stripped(self):
        text = VALID_CONFIG_TOML.replace(
            "recover_after = 2",
            'recover_after = 2\ntarget_host = " 100.64.0.10 "\nstatus_hostname = " survival.example.com "',
        )
        cfg = m.load_config(self.write_temp_config(text))
        self.assertEqual(cfg.healthcheck.target_host, "100.64.0.10")
        self.assertEqual(cfg.healthcheck.status_hostname, "survival.example.com")

    def test_readme_main_port_table_matches_example(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (REPO_ROOT / "README.de.md").read_text(encoding="utf-8")
        self.assertIn("| `main.port` | MAIN server TCP port | `25564` |", readme)
        self.assertIn("| `main.port` | TCP-Port des Hauptservers | `25564` |", readme_de)
    def test_load_config_missing_file(self):
        with self.assertRaises(m.ConfigError):
            m.load_config(REPO_ROOT / "nope.toml")

    def test_load_config_invalid_toml(self):
        p = self.write_temp_config("[proxy\nlisten_port=25565")
        with self.assertRaises(m.ConfigError):
            m.load_config(p)

    def test_load_config_section_must_be_table(self):
        with self.assertRaises(m.ConfigError):
            m.load_config(self.write_temp_config("proxy = 1"))

    def test_load_config_missing_section(self):
        with self.assertRaises(m.ConfigError):
            m.load_config(self.write_temp_config(VALID_CONFIG_TOML.replace("[fallback]\nhost = \"127.0.0.1\"\nport = 25566\n\n", "")))

    def test_load_config_missing_key(self):
        broken = VALID_CONFIG_TOML.replace("listen_port = 25565\n", "")
        with self.assertRaises(m.ConfigError):
            m.load_config(self.write_temp_config(broken))

    def test_load_config_os_error(self):
        with mock.patch("pathlib.Path.open", side_effect=OSError("permission denied")):
            with self.assertRaises(m.ConfigError):
                m.load_config(Path("config.toml"))

    def test_old_config_compatibility_defaults(self):
        cfg = m.load_config(self.write_temp_config(VALID_CONFIG_TOML))
        self.assertIsNone(cfg.healthcheck.target_host)
        self.assertIsNone(cfg.healthcheck.target_port)
        self.assertEqual(cfg.healthcheck.protocol_version, 767)
        self.assertIsNone(cfg.healthcheck.status_hostname)
        self.assertTrue(cfg.healthcheck.require_valid_json)
        self.assertFalse(cfg.healthcheck.log_status_details)

    def test_new_healthcheck_config_parsing(self):
        text = VALID_CONFIG_TOML.replace('mode = "tcp"', 'mode = "minecraft_status"').replace(
            "recover_after = 2",
            "recover_after = 2\ntarget_host = \"100.64.0.10\"\ntarget_port = 25567\nprotocol_version = 768\nstatus_hostname = \"survival.example.com\"\nrequire_valid_json = true\nlog_status_details = true",
        )
        cfg = m.load_config(self.write_temp_config(text))
        self.assertEqual(cfg.healthcheck.target_host, "100.64.0.10")
        self.assertEqual(cfg.healthcheck.target_port, 25567)
        self.assertEqual(cfg.healthcheck.protocol_version, 768)
        self.assertEqual(cfg.healthcheck.status_hostname, "survival.example.com")
        self.assertTrue(cfg.healthcheck.require_valid_json)
        self.assertTrue(cfg.healthcheck.log_status_details)

    def test_validation_new_fields(self):
        invalid_lines = [
            'target_port = "abc"',
            "target_port = 0",
            "target_port = 65536",
            'target_host = " "',
            'status_hostname = " "',
            "protocol_version = 0",
            'require_valid_json = "yes"',
            'log_status_details = "yes"',
        ]
        for line in invalid_lines:
            text = VALID_CONFIG_TOML.replace("recover_after = 2", f"recover_after = 2\n{line}")
            with self.assertRaises(m.ConfigError, msg=line):
                m.load_config(self.write_temp_config(text))

    def test_validate_config_regressions(self):
        cfg = self.valid_config()
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "proxy": m.ProxyConfig("0.0.0.0", True)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", True, 2.0, 2, 2, None, None, 767, None, True, False, 0.0)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "proxy": m.ProxyConfig("0.0.0.0", 70000)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "main": m.TargetConfig("", 25564)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", "3", 2.0, 2, 2, None, None, 767, None, True, False, 0.0)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("invalid", 3, 2, 2, 2, None, None, 767, None, True, False, 0.0)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "logging": m.LoggingConfig("NOPE")}))

    def test_validate_config_loop_detection_main_fallback_and_healthcheck(self):
        cfg = self.valid_config()
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "main": m.TargetConfig("127.0.0.1", 25565)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "fallback": m.TargetConfig("localhost", 25565)}))
        bad_hc = m.HealthCheckConfig("tcp", 3, 2, 2, 2, "127.0.0.1", 25565, 767, None, True, False, 0.0)
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "healthcheck": bad_hc}))

    def test_validate_config_safe_healthcheck_override(self):
        cfg = self.valid_config()
        good_hc = m.HealthCheckConfig("minecraft_status", 3, 2, 2, 2, "100.64.0.10", 25567, 767, None, True, False, 0.0)
        m.validate_config(m.AppConfig(**{**cfg.__dict__, "healthcheck": good_hc}))


class CoreBehaviorTests(unittest.TestCase):
    def test_health_state_threshold_behavior(self):
        state = m.HealthState(fail_after=3, recover_after=3)
        state.set_initial_state(True)
        self.assertIsNone(state.report(False))
        self.assertIsNone(state.report(False))
        self.assertFalse(state.report(False))

        state.set_initial_state(False)
        self.assertIsNone(state.report(True))
        self.assertIsNone(state.report(True))
        self.assertTrue(state.report(True))

    def test_choose_target_behavior(self):
        cfg = m.load_config(REPO_ROOT / "config.toml")
        state = m.HealthState(2, 2)
        state.set_initial_state(True)
        self.assertEqual(m.choose_target(cfg, state).name, "MAIN")
        state.set_initial_state(False)
        self.assertEqual(m.choose_target(cfg, state).name, "FALLBACK")


class StatusProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_varint_roundtrip_and_too_long(self):
        for value in (0, 1, 127, 128, 255, 2097151):
            encoded = m.write_varint(value)
            self.assertEqual(value, await m.read_varint(DummyReader(encoded)))
        with self.assertRaises(ValueError):
            await m.read_varint(DummyReader(b"\x80\x80\x80\x80\x80\x01"))

    async def test_get_healthcheck_target_overrides(self):
        cfg = m.AppConfig(
            proxy=m.ProxyConfig("0.0.0.0", 25565),
            main=m.TargetConfig("127.0.0.1", 25564),
            fallback=m.TargetConfig("127.0.0.1", 25566),
            healthcheck=m.HealthCheckConfig("tcp", 3, 2, 2, 2, None, None, 767, None, True, False, 0.0),
            connection=m.ConnectionConfig(5.0, 65536, 300.0, False, False, 4096),
            logging=m.LoggingConfig("INFO"),
        )
        self.assertEqual(m.get_healthcheck_target(cfg), m.TargetConfig("127.0.0.1", 25564))
        cfg_h = m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", 3, 2, 2, 2, "10.0.0.2", None, 767, None, True, False, 0.0)})
        self.assertEqual(m.get_healthcheck_target(cfg_h), m.TargetConfig("10.0.0.2", 25564))
        cfg_p = m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", 3, 2, 2, 2, None, 25568, 767, None, True, False, 0.0)})
        self.assertEqual(m.get_healthcheck_target(cfg_p), m.TargetConfig("127.0.0.1", 25568))
        cfg_b = m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", 3, 2, 2, 2, "10.0.0.2", 25568, 767, None, True, False, 0.0)})
        self.assertEqual(m.get_healthcheck_target(cfg_b), m.TargetConfig("10.0.0.2", 25568))

    async def test_packet_creation(self):
        packet = m.make_minecraft_status_packet("survival.example.com", 25567, 767)
        self.assertIsInstance(packet, bytes)
        self.assertIn(b"survival.example.com", packet)
        self.assertIn(m.write_varint(767), packet)
        self.assertIn(b"\x01", packet)

    async def test_minecraft_status_healthcheck_paths(self):
        async def run_server(payload: bytes):
            async def handler(reader, writer):
                await reader.read(2048)
                writer.write(payload)
                await writer.drain()
                await m.close_writer(writer)

            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            return server, server.sockets[0].getsockname()[1]

        status = b'{"version":{"name":"1.21"},"players":{"online":1,"max":20}}'
        valid_payload = m.write_varint(len(m.write_varint(0) + m.write_varint(len(status)) + status)) + (m.write_varint(0) + m.write_varint(len(status)) + status)
        server, port = await run_server(valid_payload)
        try:
            r = await m.minecraft_status_health_check("127.0.0.1", port, 1.0, 767, None, True)
            self.assertTrue(r.ok)
        finally:
            server.close(); await server.wait_closed()

        bad_id_payload = m.write_varint(len(m.write_varint(1) + m.write_varint(0))) + (m.write_varint(1) + m.write_varint(0))
        server, port = await run_server(bad_id_payload)
        try:
            self.assertFalse((await m.minecraft_status_health_check("127.0.0.1", port, 1.0, 767, None, True)).ok)
        finally:
            server.close(); await server.wait_closed()

        bad_json = b"{notjson"
        bad_json_payload = m.write_varint(len(m.write_varint(0) + m.write_varint(len(bad_json)) + bad_json)) + (m.write_varint(0) + m.write_varint(len(bad_json)) + bad_json)
        server, port = await run_server(bad_json_payload)
        try:
            self.assertFalse((await m.minecraft_status_health_check("127.0.0.1", port, 1.0, 767, None, True)).ok)
            self.assertTrue((await m.minecraft_status_health_check("127.0.0.1", port, 1.0, 767, None, False)).ok)
        finally:
            server.close(); await server.wait_closed()

        invalid_utf8 = b"\xff\xfe\xff"
        utf8_payload = m.write_varint(len(m.write_varint(0) + m.write_varint(len(invalid_utf8)) + invalid_utf8)) + (m.write_varint(0) + m.write_varint(len(invalid_utf8)) + invalid_utf8)
        server, port = await run_server(utf8_payload)
        try:
            res = await m.minecraft_status_health_check("127.0.0.1", port, 1.0, 767, None, True)
            self.assertFalse(res.ok)
            self.assertIn("invalid_utf8", res.reason)
        finally:
            server.close(); await server.wait_closed()

        oversized_json_payload = m.write_varint(len(m.write_varint(0) + m.write_varint(m.MAX_STATUS_JSON_BYTES + 1))) + (m.write_varint(0) + m.write_varint(m.MAX_STATUS_JSON_BYTES + 1))
        server, port = await run_server(oversized_json_payload)
        try:
            self.assertFalse((await m.minecraft_status_health_check("127.0.0.1", port, 1.0, 767, None, True)).ok)
        finally:
            server.close(); await server.wait_closed()

        self.assertFalse((await m.minecraft_status_health_check("127.0.0.1", 9, 0.01, 767, None, True)).ok)


if __name__ == "__main__":
    unittest.main()
