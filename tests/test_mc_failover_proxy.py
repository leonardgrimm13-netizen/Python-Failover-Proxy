import asyncio
import argparse
import json
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
min_recovery_seconds = 0.0

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
            healthcheck=m.HealthCheckConfig("tcp", 3.0, 2.0, 2, 2, 0.0, None, None, 767, None, True, False, 0.0),
            connection=m.ConnectionConfig(5.0, 65536, 300.0, False, False, 4096),
            logging=m.LoggingConfig("INFO"),
            maintenance=m.MaintenanceConfig("auto", None, None),
            monitoring=m.MonitoringConfig(False, "127.0.0.1", 8080, False),
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

    def test_readme_tables_document_new_connection_controls(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        readme_de = (REPO_ROOT / "README.de.md").read_text(encoding="utf-8")
        self.assertIn("| `healthcheck.jitter_seconds` |", readme)
        self.assertIn("| `connection.max_connections` |", readme)
        self.assertIn("| `healthcheck.jitter_seconds` |", readme_de)
        self.assertIn("| `connection.max_connections` |", readme_de)

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
        self.assertEqual(cfg.maintenance.mode, "auto")
        self.assertIsNone(cfg.maintenance.force_fallback_file)
        self.assertIsNone(cfg.maintenance.force_main_file)
        self.assertFalse(cfg.monitoring.enabled)
        self.assertEqual(cfg.monitoring.listen_host, "127.0.0.1")
        self.assertEqual(cfg.monitoring.listen_port, 8080)
        self.assertFalse(cfg.monitoring.allow_remote)

    def test_monitoring_config_validation(self):
        bad_enabled = VALID_CONFIG_TOML + '\n[monitoring]\nenabled = "yes"\n'
        with self.assertRaises(m.ConfigError):
            m.load_config(self.write_temp_config(bad_enabled))
        bad_port = VALID_CONFIG_TOML + '\n[monitoring]\nenabled = true\nlisten_port = 70000\n'
        with self.assertRaises(m.ConfigError):
            m.load_config(self.write_temp_config(bad_port))
        blocked_remote = VALID_CONFIG_TOML + '\n[monitoring]\nenabled = true\nlisten_host = "0.0.0.0"\nallow_remote = false\n'
        with self.assertRaises(m.ConfigError):
            m.load_config(self.write_temp_config(blocked_remote))
        allowed_remote = VALID_CONFIG_TOML + '\n[monitoring]\nenabled = true\nlisten_host = "0.0.0.0"\nallow_remote = true\n'
        cfg = m.load_config(self.write_temp_config(allowed_remote))
        self.assertTrue(cfg.monitoring.allow_remote)

    def test_invalid_maintenance_mode(self):
        text = VALID_CONFIG_TOML + '\n[maintenance]\nmode = "broken"\n'
        with self.assertRaises(m.ConfigError):
            m.load_config(self.write_temp_config(text))

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
            "protocol_version = 0",
            'require_valid_json = "yes"',
            'log_status_details = "yes"',
            "jitter_seconds = -0.1",
        ]
        for line in invalid_lines:
            text = VALID_CONFIG_TOML.replace("recover_after = 2", f"recover_after = 2\n{line}")
            with self.assertRaises(m.ConfigError, msg=line):
                m.load_config(self.write_temp_config(text))


    def test_min_recovery_seconds_validation_and_defaults(self):
        cfg = m.load_config(self.write_temp_config(VALID_CONFIG_TOML.replace("min_recovery_seconds = 0.0\n", "")))
        self.assertEqual(cfg.healthcheck.min_recovery_seconds, 0.0)

        for line in ["min_recovery_seconds = -1", 'min_recovery_seconds = "30"', "min_recovery_seconds = true", "min_recovery_seconds = false"]:
            text = VALID_CONFIG_TOML.replace("recover_after = 2", f"recover_after = 2\n{line}")
            with self.assertRaises(m.ConfigError, msg=line):
                m.load_config(self.write_temp_config(text))

    def test_validation_new_connection_fields(self):
        invalid_lines = [
            "buffer_size = 1",
            "idle_timeout_seconds = -1",
            "max_connections = 0",
            'connect_fallback_on_main_connect_failure = "yes"',
            'tcp_keepalive = "yes"',
        ]
        for line in invalid_lines:
            text = VALID_CONFIG_TOML.replace("buffer_size = 65536", f"buffer_size = 65536\n{line}")
            if line.startswith("buffer_size"):
                text = VALID_CONFIG_TOML.replace("buffer_size = 65536", line)
            with self.assertRaises(m.ConfigError, msg=line):
                m.load_config(self.write_temp_config(text))

    def test_validate_config_regressions(self):
        cfg = self.valid_config()
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "proxy": m.ProxyConfig("0.0.0.0", True)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", True, 2.0, 2, 2, 0.0, None, None, 767, None, True, False, 0.0)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "proxy": m.ProxyConfig("0.0.0.0", 70000)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "main": m.TargetConfig("", 25564)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", "3", 2.0, 2, 2, 0.0, None, None, 767, None, True, False, 0.0)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("invalid", 3, 2, 2, 2, 0.0, None, None, 767, None, True, False, 0.0)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "logging": m.LoggingConfig("NOPE")}))

    def test_validate_config_loop_detection_main_fallback_and_healthcheck(self):
        cfg = self.valid_config()
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "main": m.TargetConfig("127.0.0.1", 25565)}))
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "fallback": m.TargetConfig("localhost", 25565)}))
        bad_hc = m.HealthCheckConfig("tcp", 3, 2, 2, 2, 0.0, "127.0.0.1", 25565, 767, None, True, False, 0.0)
        with self.assertRaises(m.ConfigError):
            m.validate_config(m.AppConfig(**{**cfg.__dict__, "healthcheck": bad_hc}))

    def test_validate_config_safe_healthcheck_override(self):
        cfg = self.valid_config()
        good_hc = m.HealthCheckConfig("minecraft_status", 3, 2, 2, 2, 0.0, "100.64.0.10", 25567, 767, None, True, False, 0.0)
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


    def test_health_state_min_recovery_zero_behaves_like_old(self):
        state = m.HealthState(2, 2, min_recovery_seconds=0.0)
        state.set_initial_state(False)
        self.assertIsNone(state.report(True, now=100.0))
        self.assertTrue(state.report(True, now=101.0))

    def test_health_state_min_recovery_waits_until_timer_elapsed(self):
        state = m.HealthState(2, 2, min_recovery_seconds=30.0)
        state.set_initial_state(False)
        self.assertIsNone(state.report(True, now=100.0))
        self.assertIsNone(state.report(True, now=103.0))
        self.assertIsNone(state.report(True, now=129.0))
        self.assertTrue(state.report(True, now=130.0))

    def test_health_state_min_recovery_resets_on_failure(self):
        state = m.HealthState(2, 2, min_recovery_seconds=30.0)
        state.set_initial_state(False)
        self.assertIsNone(state.report(True, now=100.0))
        self.assertIsNone(state.report(False, now=110.0))
        self.assertIsNone(state.report(True, now=111.0))
        self.assertIsNone(state.report(True, now=120.0))
        self.assertTrue(state.report(True, now=141.0))

    def test_initial_healthy_not_delayed_by_min_recovery(self):
        state = m.HealthState(2, 2, min_recovery_seconds=60.0)
        state.set_initial_state(True)
        self.assertTrue(state.main_healthy)

    def test_choose_target_behavior(self):
        cfg = m.load_config(REPO_ROOT / "config.toml")
        state = m.HealthState(2, 2)
        state.set_initial_state(True)
        self.assertEqual(m.choose_target(cfg, state).name, "MAIN")
        state.set_initial_state(False)
        self.assertEqual(m.choose_target(cfg, state).name, "FALLBACK")

    def test_maintenance_mode_auto_behaves_like_health(self):
        cfg = m.load_config(REPO_ROOT / "config.toml")
        state = m.HealthState(2, 2)
        state.set_initial_state(True)
        self.assertEqual(m.choose_target_decision(cfg, state).reason, "health_main")
        state.set_initial_state(False)
        self.assertEqual(m.choose_target_decision(cfg, state).reason, "health_fallback")

    def test_force_fallback_routes_even_if_main_healthy(self):
        cfg = m.AppConfig(**{**m.load_config(REPO_ROOT / "config.toml").__dict__, "maintenance": m.MaintenanceConfig("force_fallback", None, None)})
        state = m.HealthState(2, 2)
        state.set_initial_state(True)
        decision = m.choose_target_decision(cfg, state)
        self.assertEqual(decision.target.name, "FALLBACK")
        self.assertEqual(decision.reason, "force_fallback_config")

    def test_force_main_routes_even_if_main_unhealthy(self):
        cfg = m.AppConfig(**{**m.load_config(REPO_ROOT / "config.toml").__dict__, "maintenance": m.MaintenanceConfig("force_main", None, None)})
        state = m.HealthState(2, 2)
        state.set_initial_state(False)
        decision = m.choose_target_decision(cfg, state)
        self.assertEqual(decision.target.name, "MAIN")
        self.assertEqual(decision.reason, "force_main_config")

    def test_file_overrides_and_priority_and_dynamic(self):
        with tempfile.TemporaryDirectory() as td:
            ff = Path(td) / "force_fallback"
            fm = Path(td) / "force_main"
            cfg = m.AppConfig(**{**m.load_config(REPO_ROOT / "config.toml").__dict__, "maintenance": m.MaintenanceConfig("auto", str(ff), str(fm))})
            self.assertEqual(m.get_effective_maintenance_mode(cfg), ("auto", "auto"))
            fm.touch()
            self.assertEqual(m.get_effective_maintenance_mode(cfg), ("force_main", "force_main_file"))
            ff.touch()
            self.assertEqual(m.get_effective_maintenance_mode(cfg), ("force_fallback", "force_fallback_file"))
            ff.unlink()
            self.assertEqual(m.get_effective_maintenance_mode(cfg), ("force_main", "force_main_file"))

    def test_choose_target_decision_reason_force_fallback_file(self):
        with tempfile.TemporaryDirectory() as td:
            ff = Path(td) / "force_fallback"
            ff.touch()
            cfg = m.AppConfig(**{**m.load_config(REPO_ROOT / "config.toml").__dict__, "maintenance": m.MaintenanceConfig("auto", str(ff), None)})
            state = m.HealthState(2, 2)
            state.set_initial_state(True)
            decision = m.choose_target_decision(cfg, state)
            self.assertEqual(decision.target.name, "FALLBACK")
            self.assertEqual(decision.reason, "force_fallback_file")

    def test_choose_target_decision_reason_force_main_file(self):
        with tempfile.TemporaryDirectory() as td:
            fm = Path(td) / "force_main"
            fm.touch()
            cfg = m.AppConfig(**{**m.load_config(REPO_ROOT / "config.toml").__dict__, "maintenance": m.MaintenanceConfig("auto", None, str(fm))})
            state = m.HealthState(2, 2)
            state.set_initial_state(False)
            decision = m.choose_target_decision(cfg, state)
            self.assertEqual(decision.target.name, "MAIN")
            self.assertEqual(decision.reason, "force_main_file")

    def test_choose_target_decision_both_files_prefers_force_fallback_file(self):
        with tempfile.TemporaryDirectory() as td:
            ff = Path(td) / "force_fallback"
            fm = Path(td) / "force_main"
            ff.touch()
            fm.touch()
            cfg = m.AppConfig(**{**m.load_config(REPO_ROOT / "config.toml").__dict__, "maintenance": m.MaintenanceConfig("auto", str(ff), str(fm))})
            state = m.HealthState(2, 2)
            state.set_initial_state(False)
            decision = m.choose_target_decision(cfg, state)
            self.assertEqual(decision.target.name, "FALLBACK")
            self.assertEqual(decision.reason, "force_fallback_file")


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
            healthcheck=m.HealthCheckConfig("tcp", 3, 2, 2, 2, 0.0, None, None, 767, None, True, False, 0.0),
            connection=m.ConnectionConfig(5.0, 65536, 300.0, False, False, 4096),
            logging=m.LoggingConfig("INFO"),
            maintenance=m.MaintenanceConfig("auto", None, None),
            monitoring=m.MonitoringConfig(False, "127.0.0.1", 8080, False),
        )
        self.assertEqual(m.get_healthcheck_target(cfg), m.TargetConfig("127.0.0.1", 25564))
        cfg_h = m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", 3, 2, 2, 2, 0.0, "10.0.0.2", None, 767, None, True, False, 0.0)})
        self.assertEqual(m.get_healthcheck_target(cfg_h), m.TargetConfig("10.0.0.2", 25564))
        cfg_p = m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", 3, 2, 2, 2, 0.0, None, 25568, 767, None, True, False, 0.0)})
        self.assertEqual(m.get_healthcheck_target(cfg_p), m.TargetConfig("127.0.0.1", 25568))
        cfg_b = m.AppConfig(**{**cfg.__dict__, "healthcheck": m.HealthCheckConfig("tcp", 3, 2, 2, 2, 0.0, "10.0.0.2", 25568, 767, None, True, False, 0.0)})
        self.assertEqual(m.get_healthcheck_target(cfg_b), m.TargetConfig("10.0.0.2", 25568))

    async def test_health_loop_recovery_info_is_throttled(self):
        cfg = m.load_config(REPO_ROOT / "config.toml")
        cfg = m.AppConfig(
            **{
                **cfg.__dict__,
                "healthcheck": m.HealthCheckConfig("tcp", 3.0, 2.0, 2, 3, 60.0, None, None, 767, None, False, False, 0.0),
            }
        )
        health = m.HealthState(2, 3, min_recovery_seconds=60.0)
        health.set_initial_state(False)
        stop_event = asyncio.Event()
        results = [m.HealthCheckResult(True, "ok") for _ in range(4)]
        checks = iter(results)
        monotonic_values = iter([100.0, 103.0, 106.0, 109.0])

        async def fake_check(_cfg):
            try:
                return next(checks)
            except StopIteration:
                stop_event.set()
                return m.HealthCheckResult(True, "ok")

        with mock.patch("mc_failover_proxy.check_main_server", side_effect=fake_check), mock.patch(
            "mc_failover_proxy.time", new=mock.Mock(monotonic=lambda: next(monotonic_values))
        ):
            with self.assertLogs("mc-failover", level="INFO") as captured:
                await m.health_loop(cfg, health, m.RuntimeState(started_at=0.0), stop_event)
        recovery_logs = [line for line in captured.output if "Recovery-Stabilisierung" in line]
        self.assertEqual(len(recovery_logs), 1)

    async def test_health_loop_recovery_progress_logs_after_15_seconds(self):
        cfg = m.load_config(REPO_ROOT / "config.toml")
        cfg = m.AppConfig(
            **{
                **cfg.__dict__,
                "healthcheck": m.HealthCheckConfig("tcp", 3.0, 2.0, 2, 3, 60.0, None, None, 767, None, False, False, 0.0),
            }
        )
        health = m.HealthState(2, 3, min_recovery_seconds=60.0)
        health.set_initial_state(False)
        stop_event = asyncio.Event()
        checks = iter([m.HealthCheckResult(True, "ok"), m.HealthCheckResult(True, "ok"), m.HealthCheckResult(True, "ok")])
        monotonic_values = iter([100.0, 114.0, 115.0])

        async def fake_check(_cfg):
            try:
                return next(checks)
            except StopIteration:
                stop_event.set()
                return m.HealthCheckResult(True, "ok")

        with mock.patch("mc_failover_proxy.check_main_server", side_effect=fake_check), mock.patch(
            "mc_failover_proxy.time", new=mock.Mock(monotonic=lambda: next(monotonic_values))
        ):
            with self.assertLogs("mc-failover", level="INFO") as captured:
                await m.health_loop(cfg, health, m.RuntimeState(started_at=0.0), stop_event)
        progress_logs = [line for line in captured.output if "weiterhin in Recovery-Stabilisierung" in line]
        self.assertEqual(len(progress_logs), 1)

    async def test_health_loop_logs_recovery_reset_once(self):
        cfg = m.load_config(REPO_ROOT / "config.toml")
        cfg = m.AppConfig(
            **{
                **cfg.__dict__,
                "healthcheck": m.HealthCheckConfig("tcp", 3.0, 2.0, 2, 3, 60.0, None, None, 767, None, False, False, 0.0),
            }
        )
        health = m.HealthState(2, 3, min_recovery_seconds=60.0)
        health.set_initial_state(False)
        stop_event = asyncio.Event()
        checks = iter([m.HealthCheckResult(True, "status_json_ok"), m.HealthCheckResult(False, "timeout")])
        monotonic_values = iter([100.0, 103.0])

        async def fake_check(_cfg):
            try:
                return next(checks)
            except StopIteration:
                stop_event.set()
                return m.HealthCheckResult(True, "ok")

        with mock.patch("mc_failover_proxy.check_main_server", side_effect=fake_check), mock.patch(
            "mc_failover_proxy.time", new=mock.Mock(monotonic=lambda: next(monotonic_values))
        ):
            with self.assertLogs("mc-failover", level="INFO") as captured:
                await m.health_loop(cfg, health, m.RuntimeState(started_at=0.0), stop_event)
        reset_logs = [line for line in captured.output if "Recovery wurde durch fehlgeschlagenen Healthcheck zurückgesetzt" in line]
        self.assertEqual(len(reset_logs), 1)

    async def test_health_loop_no_recovery_progress_logs_when_min_recovery_zero(self):
        cfg = m.load_config(REPO_ROOT / "config.toml")
        cfg = m.AppConfig(
            **{
                **cfg.__dict__,
                "healthcheck": m.HealthCheckConfig("tcp", 3.0, 2.0, 2, 3, 0.0, None, None, 767, None, False, False, 0.0),
            }
        )
        health = m.HealthState(2, 3, min_recovery_seconds=0.0)
        health.set_initial_state(False)
        stop_event = asyncio.Event()
        checks = iter([m.HealthCheckResult(True, "status_json_ok"), m.HealthCheckResult(True, "status_json_ok")])
        monotonic_values = iter([100.0, 103.0])

        async def fake_check(_cfg):
            try:
                return next(checks)
            except StopIteration:
                stop_event.set()
                return m.HealthCheckResult(True, "ok")

        with mock.patch("mc_failover_proxy.check_main_server", side_effect=fake_check), mock.patch(
            "mc_failover_proxy.time", new=mock.Mock(monotonic=lambda: next(monotonic_values))
        ):
            with self.assertLogs("mc-failover", level="INFO") as captured:
                await m.health_loop(cfg, health, m.RuntimeState(started_at=0.0), stop_event)
        recovery_logs = [line for line in captured.output if "Recovery-Stabilisierung" in line]
        self.assertEqual(len(recovery_logs), 0)

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


class RuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def make_config(self, main_port: int, fallback_port: int, **overrides) -> m.AppConfig:
        return m.AppConfig(
            proxy=m.ProxyConfig("127.0.0.1", 25565),
            main=m.TargetConfig("127.0.0.1", main_port),
            fallback=m.TargetConfig("127.0.0.1", fallback_port),
            healthcheck=m.HealthCheckConfig("tcp", 3.0, 1.0, 1, 1, 0.0, None, None, 767, None, True, False, 0.0),
            connection=m.ConnectionConfig(
                overrides.get("timeout_seconds", 0.5),
                4096,
                overrides.get("idle_timeout_seconds", 0.5),
                overrides.get("connect_fallback_on_main_connect_failure", True),
                False,
                overrides.get("max_connections", 1),
            ),
            logging=m.LoggingConfig("INFO"),
            maintenance=m.MaintenanceConfig("auto", None, None),
            monitoring=m.MonitoringConfig(False, "127.0.0.1", 8080, False),
        )

    async def test_connection_limiter_accepts_then_rejects(self):
        limiter = m.ConnectionLimiter(1)
        self.assertTrue(await limiter.try_acquire())
        self.assertFalse(await limiter.try_acquire())
        await limiter.release()
        self.assertTrue(await limiter.try_acquire())

    async def test_release_runs_on_connect_failure(self):
        cfg = self.make_config(9, 10, connect_fallback_on_main_connect_failure=False)
        health = m.HealthState(1, 1)
        health.set_initial_state(True)
        limiter = m.ConnectionLimiter(1)
        async def on_connect(reader, writer):
            await m.handle_client(cfg, health, limiter, m.RuntimeState(started_at=0.0), reader, writer)
        listener = await asyncio.start_server(on_connect, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.2)
        await m.close_writer(w)
        listener.close()
        await listener.wait_closed()
        self.assertTrue(await limiter.try_acquire())
        await limiter.release()

    async def test_idle_timeout_closes_inactive_connections(self):
        done = asyncio.Event()
        async def backend(reader, writer):
            await asyncio.sleep(5)
            await m.close_writer(writer)
        server = await asyncio.start_server(backend, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        cfg = self.make_config(port, port, idle_timeout_seconds=0.1)
        health = m.HealthState(1, 1); health.set_initial_state(True)
        limiter = m.ConnectionLimiter(10)
        client_server = await asyncio.start_server(lambda cr, cw: m.handle_client(cfg, health, limiter, m.RuntimeState(started_at=0.0), cr, cw), "127.0.0.1", 0)
        pport = client_server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", pport)
        await asyncio.sleep(0.3)
        data = await reader.read(1)
        self.assertEqual(data, b"")
        await m.close_writer(writer)
        client_server.close(); await client_server.wait_closed()
        server.close(); await server.wait_closed()
        done.set()

    async def test_main_fail_fallback_success_and_forward(self):
        got = asyncio.Event()
        async def fb_handler(reader, writer):
            data = await reader.read(1024)
            writer.write(data[::-1]); await writer.drain()
            got.set()
            await m.close_writer(writer)
        fb = await asyncio.start_server(fb_handler, "127.0.0.1", 0)
        fb_port = fb.sockets[0].getsockname()[1]
        cfg = self.make_config(9, fb_port, connect_fallback_on_main_connect_failure=True, max_connections=10)
        health = m.HealthState(1, 1); health.set_initial_state(True)
        limiter = m.ConnectionLimiter(10)
        proxy = await asyncio.start_server(lambda cr, cw: m.handle_client(cfg, health, limiter, m.RuntimeState(started_at=0.0), cr, cw), "127.0.0.1", 0)
        pport = proxy.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", pport)
        w.write(b"abc"); await w.drain()
        self.assertEqual(await r.read(3), b"cba")
        await got.wait()
        await m.close_writer(w)
        proxy.close(); await proxy.wait_closed()
        fb.close(); await fb.wait_closed()

    async def test_main_and_fallback_both_unreachable_client_closed(self):
        cfg = self.make_config(9, 10, connect_fallback_on_main_connect_failure=True, max_connections=10)
        health = m.HealthState(1, 1); health.set_initial_state(True)
        limiter = m.ConnectionLimiter(10)
        proxy = await asyncio.start_server(lambda cr, cw: m.handle_client(cfg, health, limiter, m.RuntimeState(started_at=0.0), cr, cw), "127.0.0.1", 0)
        pport = proxy.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", pport)
        await asyncio.sleep(0.2)
        self.assertEqual(await r.read(1), b"")
        await m.close_writer(w)
        proxy.close(); await proxy.wait_closed()

    async def test_connection_rejected_and_active_counter(self):
        hold = asyncio.Event()
        release = asyncio.Event()

        async def backend(reader, writer):
            hold.set()
            await release.wait()
            await m.close_writer(writer)

        target = await asyncio.start_server(backend, "127.0.0.1", 0)
        port = target.sockets[0].getsockname()[1]
        cfg = self.make_config(port, port, max_connections=1)
        health = m.HealthState(1, 1); health.set_initial_state(True)
        limiter = m.ConnectionLimiter(1)
        state = m.RuntimeState(started_at=0.0)
        proxy = await asyncio.start_server(lambda cr, cw: m.handle_client(cfg, health, limiter, state, cr, cw), "127.0.0.1", 0)
        pport = proxy.sockets[0].getsockname()[1]
        r1, w1 = await asyncio.open_connection("127.0.0.1", pport)
        await hold.wait()
        r2, w2 = await asyncio.open_connection("127.0.0.1", pport)
        await asyncio.sleep(0.1)
        self.assertGreaterEqual(state.rejected_connections, 1)
        self.assertGreaterEqual(state.active_connections, 1)
        release.set()
        await asyncio.sleep(0.2)
        self.assertEqual(state.active_connections, 0)
        await m.close_writer(w1); await m.close_writer(w2)
        _ = await r1.read(1); _ = await r2.read(1)
        proxy.close(); await proxy.wait_closed()
        target.close(); await target.wait_closed()


class MonitoringTests(unittest.IsolatedAsyncioTestCase):
    async def test_monitoring_endpoints(self):
        cfg = m.load_config(REPO_ROOT / "config.example.toml")
        cfg = m.AppConfig(**{**cfg.__dict__, "monitoring": m.MonitoringConfig(True, "127.0.0.1", 0, False)})
        health = m.HealthState(1, 1); health.set_initial_state(True)
        state = m.RuntimeState(started_at=1.0, active_connections=2, total_connections=5, rejected_connections=1, active_target="FALLBACK", routing_reason="health_fallback")
        state.last_health_result = m.HealthCheckResult(ok=True, reason="status_json_ok", latency_ms=12.3)
        state.last_health_check_at = 2.0
        server = await m.start_monitoring_server(cfg, health, state, asyncio.Event())
        port = server.sockets[0].getsockname()[1]
        try:
            async def req(raw: bytes) -> bytes:
                r, w = await asyncio.open_connection("127.0.0.1", port)
                w.write(raw); await w.drain()
                data = await r.read(65535)
                await m.close_writer(w)
                return data

            health_resp = await req(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertIn(b"200 OK", health_resp)
            self.assertIn(b'"service": "mc-failover"', health_resp)
            state_resp = await req(b"GET /state HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertIn(b"active_connections", state_resp)
            metrics_resp = await req(b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertIn(b"mc_failover_total_connections", metrics_resp)
            ready_resp = await req(b"GET /ready HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertIn(b'"active_target": "FALLBACK"', ready_resp)
            self.assertIn(b'"main_healthy": true', ready_resp)
            not_found = await req(b"GET /nope HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertIn(b"404 Not Found", not_found)
            method = await req(b"POST /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertIn(b"405 Method Not Allowed", method)
            too_many_headers = b"GET /health HTTP/1.1\r\n" + b"X-A: b\r\n" * 70 + b"\r\n"
            bad = await req(too_many_headers)
            self.assertIn(b"400 Bad Request", bad)
        finally:
            server.close(); await server.wait_closed()


class CliChecksTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_args_accepts_new_flags(self):
        argv = ["prog", "--check-config", "--test-main", "--test-fallback", "--test-healthcheck", "--print-effective-config"]
        with mock.patch("sys.argv", argv):
            args = m.parse_args()
        self.assertTrue(args.check_config)
        self.assertTrue(args.test_main)
        self.assertTrue(args.test_fallback)
        self.assertTrue(args.test_healthcheck)
        self.assertTrue(args.print_effective_config)

    async def test_check_config_valid_returns_zero(self):
        cfg = m.load_config(REPO_ROOT / "config.example.toml")
        args = argparse.Namespace(check_config=True, print_effective_config=False, test_main=False, test_fallback=False, test_healthcheck=False, config=Path("config.example.toml"))
        self.assertEqual(await m.run_cli_checks(args, cfg), 0)

    async def test_check_config_invalid_returns_one_via_run(self):
        with mock.patch("mc_failover_proxy.parse_args", return_value=argparse.Namespace(config=Path("missing.toml"), check_config=True, print_effective_config=False, test_main=False, test_fallback=False, test_healthcheck=False)):
            self.assertEqual(await m.run(), 1)

    async def test_check_config_does_not_start_server(self):
        cfg = m.load_config(REPO_ROOT / "config.example.toml")
        args = argparse.Namespace(check_config=True, print_effective_config=False, test_main=False, test_fallback=False, test_healthcheck=False, config=Path("config.example.toml"))
        with mock.patch("asyncio.start_server", new=mock.AsyncMock()) as mocked_start:
            self.assertEqual(await m.run_cli_checks(args, cfg), 0)
            mocked_start.assert_not_awaited()

    async def test_print_effective_config_outputs_valid_json(self):
        cfg = m.load_config(REPO_ROOT / "config.example.toml")
        args = argparse.Namespace(check_config=False, print_effective_config=True, test_main=False, test_fallback=False, test_healthcheck=False, config=Path("config.example.toml"))
        with mock.patch("sys.stdout.write") as write_mock:
            self.assertEqual(await m.run_cli_checks(args, cfg), 0)
        emitted = "".join(call.args[0] for call in write_mock.call_args_list)
        data = json.loads(emitted)
        for key in ["proxy", "main", "fallback", "healthcheck", "connection", "logging", "maintenance"]:
            self.assertIn(key, data)

    async def test_test_main_uses_main_target(self):
        cfg = m.load_config(REPO_ROOT / "config.example.toml")
        args = argparse.Namespace(check_config=False, print_effective_config=False, test_main=True, test_fallback=False, test_healthcheck=False, config=Path("config.example.toml"))
        with mock.patch("mc_failover_proxy.tcp_health_check", new=mock.AsyncMock(return_value=m.HealthCheckResult(True, "ok", 1.2))) as mocked_tcp:
            self.assertEqual(await m.run_cli_checks(args, cfg), 0)
            mocked_tcp.assert_awaited_once_with(cfg.main.host, cfg.main.port, cfg.connection.timeout_seconds)

    async def test_test_fallback_uses_fallback_target(self):
        cfg = m.load_config(REPO_ROOT / "config.example.toml")
        args = argparse.Namespace(check_config=False, print_effective_config=False, test_main=False, test_fallback=True, test_healthcheck=False, config=Path("config.example.toml"))
        with mock.patch("mc_failover_proxy.tcp_health_check", new=mock.AsyncMock(return_value=m.HealthCheckResult(True, "ok", 1.2))) as mocked_tcp:
            self.assertEqual(await m.run_cli_checks(args, cfg), 0)
            mocked_tcp.assert_awaited_once_with(cfg.fallback.host, cfg.fallback.port, cfg.connection.timeout_seconds)

    async def test_test_healthcheck_uses_check_main_server(self):
        cfg = m.load_config(REPO_ROOT / "config.example.toml")
        args = argparse.Namespace(check_config=False, print_effective_config=False, test_main=False, test_fallback=False, test_healthcheck=True, config=Path("config.example.toml"))
        with mock.patch("mc_failover_proxy.check_main_server", new=mock.AsyncMock(return_value=m.HealthCheckResult(True, "ok", 1.2))) as mocked_check:
            self.assertEqual(await m.run_cli_checks(args, cfg), 0)
            mocked_check.assert_awaited_once_with(cfg)

    async def test_multiple_flags_fail_if_one_check_fails(self):
        cfg = m.load_config(REPO_ROOT / "config.example.toml")
        args = argparse.Namespace(check_config=True, print_effective_config=False, test_main=True, test_fallback=True, test_healthcheck=False, config=Path("config.example.toml"))
        async def tcp_side_effect(host, port, timeout):
            if port == cfg.main.port:
                return m.HealthCheckResult(ok=False, reason="down")
            return m.HealthCheckResult(ok=True, reason="ok", latency_ms=1.0)
        with mock.patch("mc_failover_proxy.tcp_health_check", new=mock.AsyncMock(side_effect=tcp_side_effect)):
            self.assertEqual(await m.run_cli_checks(args, cfg), 1)

if __name__ == "__main__":
    unittest.main()
