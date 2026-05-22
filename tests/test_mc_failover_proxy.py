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


class VarIntTests(unittest.IsolatedAsyncioTestCase):
    async def test_varint_roundtrip(self):
        for value in (0, 1, 127, 128, 255, 2097151):
            encoded = m.write_varint(value)
            decoded = await m.read_varint(DummyReader(encoded))
            self.assertEqual(value, decoded)

    async def test_varint_too_long(self):
        with self.assertRaises(ValueError):
            await m.read_varint(DummyReader(b"\x80\x80\x80\x80\x80"))


class HealthStateTests(unittest.TestCase):
    def test_initial_healthy(self):
        state = m.HealthState()
        state.set_initial_state(True)
        self.assertTrue(state.main_healthy)

    def test_initial_unhealthy(self):
        state = m.HealthState()
        state.set_initial_state(False)
        self.assertFalse(state.main_healthy)

    def test_failover_threshold(self):
        state = m.HealthState()
        state.set_initial_state(True)
        self.assertIsNone(state.report(False))
        self.assertFalse(state.report(False))

    def test_recovery_threshold(self):
        state = m.HealthState()
        state.set_initial_state(False)
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


class ConfigValidationTests(unittest.TestCase):
    def test_validate_config_ok(self):
        m.validate_config()

    def test_invalid_health_check_mode(self):
        old = m.HEALTH_CHECK_MODE
        try:
            m.HEALTH_CHECK_MODE = "invalid"
            with self.assertRaises(ValueError):
                m.validate_config()
        finally:
            m.HEALTH_CHECK_MODE = old


if __name__ == "__main__":
    unittest.main()
