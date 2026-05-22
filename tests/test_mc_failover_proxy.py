import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mc_failover_proxy as m


VALID_CONFIG_TOML = """[proxy]
listen_host = \"0.0.0.0\"
listen_port = 25565

[main]
host = \"127.0.0.1\"
port = 25567

[fallback]
host = \"127.0.0.1\"
port = 25566

[healthcheck]
mode = \"tcp\"
interval_seconds = 3.0
timeout_seconds = 2.0
fail_after = 2
recover_after = 2

[connection]
timeout_seconds = 5.0
buffer_size = 65536

[logging]
level = \"INFO\"
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


class VarIntTests(unittest.IsolatedAsyncioTestCase):
    async def test_varint_roundtrip(self):
        for value in (0, 1, 127, 128, 255, 2097151):
            encoded = m.write_varint(value)
            decoded = await m.read_varint(DummyReader(encoded))
            self.assertEqual(value, decoded)


class ConfigTests(unittest.TestCase):
    def valid_config(self) -> m.AppConfig:
        return m.AppConfig(
            proxy=m.ProxyConfig("0.0.0.0", 25565),
            main=m.TargetConfig("127.0.0.1", 25567),
            fallback=m.TargetConfig("127.0.0.1", 25566),
            healthcheck=m.HealthCheckConfig("tcp", 3.0, 2.0, 2, 2),
            connection=m.ConnectionConfig(5.0, 65536),
            logging=m.LoggingConfig("INFO"),
        )

    def write_temp_config(self, text: str = VALID_CONFIG_TOML) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "config.toml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_load_config_ok(self):
        cfg = m.load_config(self.write_temp_config())
        self.assertEqual(cfg.proxy.listen_port, 25565)

    def test_repo_config_toml_is_valid(self):
        cfg = m.load_config(Path("config.toml"))
        self.assertEqual(cfg.logging.level, "INFO")

    def test_load_config_missing(self):
        with self.assertRaises(m.ConfigError):
            m.load_config(Path("does-not-exist.toml"))

    def test_load_config_invalid_toml(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "broken.toml"
            p.write_text("[proxy\nlisten_port=25565", encoding="utf-8")
            with self.assertRaises(m.ConfigError):
                m.load_config(p)

    def test_validate_config_ok(self):
        m.validate_config(self.valid_config())

    def test_load_config_section_must_be_table(self):
        p = self.write_temp_config("proxy = 123")
        with self.assertRaises(m.ConfigError):
            m.load_config(p)

    def test_load_config_os_error(self):
        with mock.patch("pathlib.Path.open", side_effect=OSError("permission denied")):
            with self.assertRaises(m.ConfigError):
                m.load_config(Path("config.toml"))

    def test_validate_config_rejects_bool_as_int(self):
        cfg = self.valid_config()
        cfg = m.AppConfig(**{**cfg.__dict__, "proxy": m.ProxyConfig("0.0.0.0", True)})
        with self.assertRaises(m.ConfigError):
            m.validate_config(cfg)

    def test_validate_config_invalid_port(self):
        cfg = self.valid_config()
        cfg = m.AppConfig(**{**cfg.__dict__, "proxy": m.ProxyConfig("0.0.0.0", 70000)})
        with self.assertRaises(m.ConfigError):
            m.validate_config(cfg)

    def test_validate_config_empty_host(self):
        cfg = self.valid_config()
        cfg = m.AppConfig(**{**cfg.__dict__, "main": m.TargetConfig("", 25567)})
        with self.assertRaises(m.ConfigError):
            m.validate_config(cfg)

    def test_validate_config_bad_timeout_type(self):
        cfg = self.valid_config()
        cfg = m.AppConfig(
            **{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", "3", 2.0, 2, 2)}
        )
        with self.assertRaises(m.ConfigError):
            m.validate_config(cfg)

    def test_validate_config_invalid_mode(self):
        cfg = self.valid_config()
        cfg = m.AppConfig(
            **{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("invalid", 3.0, 2.0, 2, 2)}
        )
        with self.assertRaises(m.ConfigError):
            m.validate_config(cfg)

    def test_validate_config_invalid_log_level(self):
        cfg = self.valid_config()
        cfg = m.AppConfig(**{**cfg.__dict__, "logging": m.LoggingConfig("NOPE")})
        with self.assertRaises(m.ConfigError):
            m.validate_config(cfg)

    def test_loop_detect_any_to_loopback(self):
        cfg = self.valid_config()
        cfg = m.AppConfig(**{**cfg.__dict__, "main": m.TargetConfig("127.0.0.1", 25565)})
        with self.assertRaises(m.ConfigError):
            m.validate_config(cfg)

    def test_loop_detect_any_to_localhost(self):
        cfg = self.valid_config()
        cfg = m.AppConfig(**{**cfg.__dict__, "fallback": m.TargetConfig("localhost", 25565)})
        with self.assertRaises(m.ConfigError):
            m.validate_config(cfg)


class HealthAndTargetTests(unittest.TestCase):
    def test_health_state_thresholds(self):
        state = m.HealthState(fail_after=3, recover_after=3)
        state.set_initial_state(True)
        self.assertIsNone(state.report(False))
        self.assertIsNone(state.report(False))
        self.assertFalse(state.report(False))

        state.set_initial_state(False)
        self.assertIsNone(state.report(True))
        self.assertIsNone(state.report(True))
        self.assertTrue(state.report(True))

    def test_choose_target(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.toml"
            cfg_path.write_text(VALID_CONFIG_TOML, encoding="utf-8")
            cfg = m.load_config(cfg_path)
        state = m.HealthState(fail_after=2, recover_after=2)
        state.set_initial_state(True)
        self.assertEqual(m.choose_target(cfg, state).name, "MAIN")
        state.set_initial_state(False)
        self.assertEqual(m.choose_target(cfg, state).name, "FALLBACK")


if __name__ == "__main__":
    unittest.main()
