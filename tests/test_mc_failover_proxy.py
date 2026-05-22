import asyncio
import unittest

import mc_failover_proxy as m


class DummyReader:
    def __init__(self, data: bytes):
        self._data = bytearray(data)

    async def readexactly(self, n: int) -> bytes:
        if len(self._data) < n:
            raise asyncio.IncompleteReadError(partial=bytes(self._data), expected=n)
        out = bytes(self._data[:n])
        del self._data[:n]
        return out


class DummyConfigPatchMixin:
    PATCH_KEYS = (
        "LISTEN_HOST",
        "LISTEN_PORT",
        "MAIN_HOST",
        "MAIN_PORT",
        "FALLBACK_HOST",
        "FALLBACK_PORT",
        "HEALTH_CHECK_MODE",
        "CHECK_INTERVAL_SECONDS",
        "CHECK_TIMEOUT_SECONDS",
        "CONNECTION_TIMEOUT_SECONDS",
        "FAIL_AFTER",
        "RECOVER_AFTER",
        "BUFFER_SIZE",
        "LOG_LEVEL",
    )

    def setUp(self):
        self.old_values = {key: getattr(m, key) for key in self.PATCH_KEYS}

    def tearDown(self):
        for key, value in self.old_values.items():
            setattr(m, key, value)


class VarIntTests(unittest.IsolatedAsyncioTestCase):
    async def test_varint_roundtrip(self):
        for value in (0, 1, 127, 128, 255, 2097151):
            encoded = m.write_varint(value)
            decoded = await m.read_varint(DummyReader(encoded))
            self.assertEqual(value, decoded)

    async def test_varint_too_long(self):
        with self.assertRaises(ValueError):
            await m.read_varint(DummyReader(b"\x80\x80\x80\x80\x80"))


class HealthStateTests(DummyConfigPatchMixin, unittest.TestCase):
    def test_initial_healthy(self):
        state = m.HealthState()
        state.set_initial_state(True)
        self.assertTrue(state.main_healthy)

    def test_initial_unhealthy(self):
        state = m.HealthState()
        state.set_initial_state(False)
        self.assertFalse(state.main_healthy)

    def test_failover_threshold(self):
        m.FAIL_AFTER = 3
        state = m.HealthState()
        state.set_initial_state(True)
        for _ in range(m.FAIL_AFTER - 1):
            self.assertIsNone(state.report(False))
        self.assertFalse(state.report(False))

    def test_recovery_threshold(self):
        m.RECOVER_AFTER = 3
        state = m.HealthState()
        state.set_initial_state(False)
        for _ in range(m.RECOVER_AFTER - 1):
            self.assertIsNone(state.report(True))
        self.assertTrue(state.report(True))


class TargetTests(unittest.TestCase):
    def test_choose_target(self):
        old = m.health
        try:
            m.health = m.HealthState()
            m.health.set_initial_state(True)
            self.assertEqual(m.choose_target().name, "MAIN")
            m.health.set_initial_state(False)
            self.assertEqual(m.choose_target().name, "FALLBACK")
        finally:
            m.health = old


class ConfigValidationTests(DummyConfigPatchMixin, unittest.TestCase):
    def test_validate_config_ok(self):
        m.LISTEN_HOST = "0.0.0.0"
        m.LISTEN_PORT = 25565
        m.MAIN_HOST = "127.0.0.1"
        m.MAIN_PORT = 25567
        m.FALLBACK_HOST = "127.0.0.1"
        m.FALLBACK_PORT = 25566
        m.validate_config()

    def test_invalid_health_check_mode(self):
        m.HEALTH_CHECK_MODE = "invalid"
        with self.assertRaises(ValueError):
            m.validate_config()

    def test_invalid_host_values(self):
        for key, value in (("LISTEN_HOST", ""), ("MAIN_HOST", None), ("FALLBACK_HOST", "  ")):
            with self.subTest(key=key, value=value):
                setattr(m, key, value)
                with self.assertRaises(ValueError):
                    m.validate_config()
                setattr(m, key, self.old_values[key])

    def test_invalid_timeout_type(self):
        m.CHECK_INTERVAL_SECONDS = "3"
        with self.assertRaises(ValueError):
            m.validate_config()

    def test_invalid_log_level(self):
        m.LOG_LEVEL = "NOPE"
        with self.assertRaises(ValueError):
            m.validate_config()

    def test_loop_detect_listen_any_to_main_loopback_same_port(self):
        m.LISTEN_HOST = "0.0.0.0"
        m.LISTEN_PORT = 25565
        m.MAIN_HOST = "127.0.0.1"
        m.MAIN_PORT = 25565
        with self.assertRaises(ValueError):
            m.validate_config()

    def test_loop_detect_listen_any_to_fallback_localhost_same_port(self):
        m.LISTEN_HOST = "0.0.0.0"
        m.LISTEN_PORT = 25565
        m.FALLBACK_HOST = "localhost"
        m.FALLBACK_PORT = 25565
        with self.assertRaises(ValueError):
            m.validate_config()


if __name__ == "__main__":
    unittest.main()
