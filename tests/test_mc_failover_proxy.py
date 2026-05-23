import tempfile, unittest
from pathlib import Path
import mc_failover_proxy as m

class IntegrationTests(unittest.TestCase):
    def setUp(self): self.repo=Path(__file__).resolve().parents[1]
    def test_example_loads(self):
        c=m.load_config(self.repo/'config.example.toml')
        self.assertEqual(c.monitoring.listen_port,8080)
        self.assertEqual(c.proxy_protocol.version,1)
    def test_old_compat_defaults(self):
        txt='''[proxy]\nlisten_host="0.0.0.0"\nlisten_port=25565\n[main]\nhost="127.0.0.1"\nport=25564\n[fallback]\nhost="127.0.0.1"\nport=25566\n[healthcheck]\nmode="tcp"\ninterval_seconds=1\ntimeout_seconds=1\nfail_after=1\nrecover_after=1\n[connection]\ntimeout_seconds=1\nbuffer_size=1024\n[logging]\nlevel="INFO"\n'''
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'c.toml'; p.write_text(txt)
            c=m.load_config(p)
            self.assertEqual(c.maintenance.mode,'auto')
            self.assertFalse(c.monitoring.enabled)
    def test_health_recovery_wait(self):
        h=m.HealthState(1,2,3.0); h.set_initial_state(False)
        self.assertIsNone(h.report(True,now=0.0)); self.assertIsNone(h.report(True,now=1.0)); self.assertFalse(h.main_healthy)
        self.assertTrue(h.report(True,now=4.2))
    def test_choose_target_decision(self):
        c=m.load_config(self.repo/'config.example.toml'); h=m.HealthState(1,1); h.set_initial_state(False)
        d=m.choose_target_decision(c,h); self.assertEqual(d.reason,'health_fallback')
    def test_extract_motd(self):
        t=m.extract_motd_text({'text':'Hello','extra':[{'text':'World'}]}); self.assertIn('Hello',t); self.assertIn('World',t)

if __name__=='__main__': unittest.main()
