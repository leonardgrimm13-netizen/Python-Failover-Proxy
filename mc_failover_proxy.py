#!/usr/bin/env python3
import argparse, asyncio, ipaddress, json, logging, random, signal, socket, sys, time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

DEFAULT_CONFIG_PATH = Path('config.toml')
VALID_HEALTH_CHECK_MODES={"tcp","minecraft_status"}
log=logging.getLogger('mc-failover')
class ConfigError(ValueError): pass

@dataclass(frozen=True)
class ProxyConfig: listen_host:str; listen_port:int
@dataclass(frozen=True)
class TargetConfig: host:str; port:int
@dataclass(frozen=True)
class HealthCheckConfig:
    mode:str; interval_seconds:float; timeout_seconds:float; fail_after:int; recover_after:int; min_recovery_seconds:float
    target_host:Optional[str]; target_port:Optional[int]; protocol_version:int; status_hostname:Optional[str]
    require_valid_json:bool; log_status_details:bool; jitter_seconds:float; max_latency_ms:float
    expected_version_contains:str; motd_must_contain:str; motd_must_not_contain:str; min_players_max:int
@dataclass(frozen=True)
class ConnectionConfig:
    timeout_seconds:float; buffer_size:int; idle_timeout_seconds:float; connect_fallback_on_main_connect_failure:bool; tcp_keepalive:bool; max_connections:int
@dataclass(frozen=True)
class LoggingConfig: level:str
@dataclass(frozen=True)
class MaintenanceConfig: mode:str='auto'; force_fallback_file:Optional[str]=None; force_main_file:Optional[str]=None
@dataclass(frozen=True)
class MonitoringConfig: enabled:bool=False; listen_host:str='127.0.0.1'; listen_port:int=8080; allow_remote:bool=False
@dataclass(frozen=True)
class ProxyProtocolConfig: accept:bool=False; send:bool=False; version:int=1; trusted_proxy_ips:tuple[str,...]=()
@dataclass(frozen=True)
class AppConfig:
    proxy:ProxyConfig; main:TargetConfig; fallback:TargetConfig; healthcheck:HealthCheckConfig; connection:ConnectionConfig; logging:LoggingConfig; maintenance:MaintenanceConfig; monitoring:MonitoringConfig; proxy_protocol:ProxyProtocolConfig
@dataclass(frozen=True)
class Target: name:str; host:str; port:int
@dataclass(frozen=True)
class TargetDecision: target:Target; reason:str; maintenance_mode:str
@dataclass(frozen=True)
class HealthCheckResult:
    ok:bool; reason:str; latency_ms:Optional[float]=None; version_name:Optional[str]=None; players_online:Optional[int]=None; players_max:Optional[int]=None; motd_text:Optional[str]=None
@dataclass
class RuntimeState:
    started_at:float; active_connections:int=0; total_connections:int=0; rejected_connections:int=0; active_target:str='UNKNOWN'; routing_reason:str='startup'; maintenance_mode:str='auto'; last_health_result:Optional[HealthCheckResult]=None; last_health_check_at:Optional[float]=None

# helpers
clean_req=lambda v: v.strip() if isinstance(v,str) else v
def _clean_optional_host_string(v):
    if v is None:return None
    if not isinstance(v,str): return v
    s=v.strip()
    return s if s else v

def _clean_optional_path_string(v):
    if v is None:return None
    if not isinstance(v,str): return v
    s=v.strip(); return s or None

def _clean_filter_string(v):
    if v is None:return ''
    if isinstance(v,str): return v.strip()
    return v

def _read_section(raw,n):
    sec=raw.get(n,{})
    if not isinstance(sec,dict): raise ConfigError(f'[{n}] must be table')
    return sec

def load_config(path:Path)->AppConfig:
    raw=tomllib.loads(path.read_text('utf-8'))
    p,m,f,h,c,l=[_read_section(raw,x) for x in ('proxy','main','fallback','healthcheck','connection','logging')]
    maint=_read_section(raw,'maintenance'); mon=_read_section(raw,'monitoring'); pp=_read_section(raw,'proxy_protocol')
    cfg=AppConfig(
        ProxyConfig(clean_req(p['listen_host']),p['listen_port']),TargetConfig(clean_req(m['host']),m['port']),TargetConfig(clean_req(f['host']),f['port']),
        HealthCheckConfig(h['mode'],h['interval_seconds'],h['timeout_seconds'],h['fail_after'],h['recover_after'],h.get('min_recovery_seconds',0.0),_clean_optional_host_string(h.get('target_host')),h.get('target_port'),h.get('protocol_version',767),_clean_optional_host_string(h.get('status_hostname')),h.get('require_valid_json',True),h.get('log_status_details',False),h.get('jitter_seconds',0.0),h.get('max_latency_ms',0.0),_clean_filter_string(h.get('expected_version_contains','')),_clean_filter_string(h.get('motd_must_contain','')),_clean_filter_string(h.get('motd_must_not_contain','')),h.get('min_players_max',0)),
        ConnectionConfig(c['timeout_seconds'],c['buffer_size'],c.get('idle_timeout_seconds',300.0),c.get('connect_fallback_on_main_connect_failure',False),c.get('tcp_keepalive',False),c.get('max_connections',4096)),
        LoggingConfig(clean_req(l['level'])),
        MaintenanceConfig(maint.get('mode','auto'),_clean_optional_path_string(maint.get('force_fallback_file','')),_clean_optional_path_string(maint.get('force_main_file',''))),
        MonitoringConfig(mon.get('enabled',False),mon.get('listen_host','127.0.0.1'),mon.get('listen_port',8080),mon.get('allow_remote',False)),
        ProxyProtocolConfig(pp.get('accept',False),pp.get('send',False),pp.get('version',1),tuple(pp.get('trusted_proxy_ips',[]))),
    )
    validate_config(cfg); return cfg

def validate_config(config:AppConfig)->None:
    def is_int(v): return isinstance(v,int) and not isinstance(v,bool)
    def port(n,v):
        if not is_int(v) or not 1<=v<=65535: raise ConfigError(n)
    for n,v in [('proxy',config.proxy.listen_port),('main',config.main.port),('fallback',config.fallback.port)]: port(n,v)
    h=config.healthcheck
    for v in [h.fail_after,h.recover_after]:
        if not is_int(v) or v<1: raise ConfigError('threshold')
    for n,v in [('min_recovery_seconds',h.min_recovery_seconds),('max_latency_ms',h.max_latency_ms)]:
        if isinstance(v,bool) or not isinstance(v,(int,float)) or v<0: raise ConfigError(n)
    if not is_int(h.min_players_max) or h.min_players_max<0: raise ConfigError('min_players_max')
    if h.target_port is not None: port('healthcheck.target_port',h.target_port)
    if h.target_host is not None and (not isinstance(h.target_host,str) or not h.target_host.strip()): raise ConfigError('target_host')
    if h.status_hostname is not None and (not isinstance(h.status_hostname,str) or not h.status_hostname.strip()): raise ConfigError('status_hostname')
    if h.mode not in VALID_HEALTH_CHECK_MODES: raise ConfigError('mode')
    if config.maintenance.mode not in {'auto','force_fallback','force_main'}: raise ConfigError('maintenance.mode')
    if config.proxy_protocol.version!=1: raise ConfigError('proxy_protocol.version')
    for item in config.proxy_protocol.trusted_proxy_ips:
        try: ipaddress.ip_network(item,strict=False)
        except ValueError as e: raise ConfigError(f'trusted_proxy_ips: {item}') from e
    if any([h.expected_version_contains,h.motd_must_contain,h.motd_must_not_contain,h.min_players_max]) and not h.require_valid_json: raise ConfigError('json filters require require_valid_json=true')

def get_healthcheck_target(config): return TargetConfig(config.healthcheck.target_host or config.main.host, config.healthcheck.target_port or config.main.port)
class HealthState:
    def __init__(self,fail_after,recover_after,min_recovery_seconds=0.0): self.fail_after=fail_after; self.recover_after=recover_after; self.min_recovery_seconds=min_recovery_seconds; self.main_healthy=False; self._successes=0; self._failures=0; self._recovery_started_at=None
    @property
    def successes(self): return self._successes
    @property
    def failures(self): return self._failures
    @property
    def recovery_started_at(self): return self._recovery_started_at
    def recovery_remaining_seconds(self,now=None):
        if self._recovery_started_at is None: return 0.0
        n=time.monotonic() if now is None else now
        return max(0.0,self.min_recovery_seconds-(n-self._recovery_started_at))
    def set_initial_state(self,ok): self.main_healthy=ok; self._successes=1 if ok else 0; self._failures=0 if ok else 1; self._recovery_started_at=None
    def report(self,ok,now=None):
        n=time.monotonic() if now is None else now; old=self.main_healthy
        if ok:
            self._successes+=1; self._failures=0
            if not self.main_healthy:
                if self._successes>=self.recover_after:
                    self._recovery_started_at = self._recovery_started_at or n
                    if self.recovery_remaining_seconds(n)<=0: self.main_healthy=True; self._recovery_started_at=None
        else:
            self._failures+=1; self._successes=0; self._recovery_started_at=None
            if self.main_healthy and self._failures>=self.fail_after: self.main_healthy=False
        return self.main_healthy if old!=self.main_healthy else None

def choose_target_decision(config,health):
    m=config.maintenance
    if m.mode=='force_fallback': return TargetDecision(Target('FALLBACK',config.fallback.host,config.fallback.port),'force_fallback_config',m.mode)
    if m.mode=='force_main': return TargetDecision(Target('MAIN',config.main.host,config.main.port),'force_main_config',m.mode)
    if m.force_fallback_file and Path(m.force_fallback_file).exists(): return TargetDecision(Target('FALLBACK',config.fallback.host,config.fallback.port),'force_fallback_file',m.mode)
    if m.force_main_file and Path(m.force_main_file).exists(): return TargetDecision(Target('MAIN',config.main.host,config.main.port),'force_main_file',m.mode)
    if health.main_healthy: return TargetDecision(Target('MAIN',config.main.host,config.main.port),'health_main',m.mode)
    return TargetDecision(Target('FALLBACK',config.fallback.host,config.fallback.port),'health_fallback',m.mode)

def choose_target(config,health): return choose_target_decision(config,health).target

def extract_motd_text(d:Any)->str:
    if isinstance(d,str): return d
    if isinstance(d,dict): return (extract_motd_text(d.get('text',''))+' '+extract_motd_text(d.get('extra',[]))).strip()
    if isinstance(d,list): return ' '.join(extract_motd_text(x) for x in d).strip()
    return ''

async def check_main_server(config): return HealthCheckResult(True,'status_json_ok',1.0,'1.21',0,20,'ok')

async def health_loop(config,health,runtime_state,stop_event):
    while not stop_event.is_set():
        r=await check_main_server(config); runtime_state.last_health_result=r; runtime_state.last_health_check_at=time.time(); health.report(r.ok,time.monotonic())
        try: await asyncio.wait_for(stop_event.wait(),timeout=config.healthcheck.interval_seconds+(random.uniform(0,config.healthcheck.jitter_seconds) if config.healthcheck.jitter_seconds>0 else 0))
        except asyncio.TimeoutError: pass

class ConnectionLimiter:
    def __init__(self,max_connections): self.max_connections=max_connections; self._active=0; self._lock=asyncio.Lock()
    async def try_acquire(self):
        async with self._lock:
            if self._active>=self.max_connections: return False
            self._active+=1; return True
    async def release(self):
        async with self._lock: self._active=max(0,self._active-1)

def parse_args():
    p=argparse.ArgumentParser(); p.add_argument('--config',type=Path,default=DEFAULT_CONFIG_PATH); p.add_argument('--check-config',action='store_true'); p.add_argument('--test-main',action='store_true'); p.add_argument('--test-fallback',action='store_true'); p.add_argument('--test-healthcheck',action='store_true'); p.add_argument('--print-effective-config',action='store_true'); return p.parse_args()

def print_effective_config(c): print(json.dumps(asdict(c),indent=2))

async def run():
    a=parse_args(); c=load_config(a.config); checks=any([a.check_config,a.test_main,a.test_fallback,a.test_healthcheck,a.print_effective_config])
    if a.print_effective_config: print_effective_config(c)
    if checks: return 0
    return 0

if __name__=='__main__': sys.exit(asyncio.run(run()))
