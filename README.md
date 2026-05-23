# Minecraft Python Failover Proxy

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: Linux](https://img.shields.io/badge/Platform-Linux-informational.svg)](https://kernel.org/)
[![Tests](https://github.com/leonardgrimm13-netizen/Minecraft-Python-Failover-Proxy/actions/workflows/tests.yml/badge.svg)](https://github.com/leonardgrimm13-netizen/Minecraft-Python-Failover-Proxy/actions/workflows/tests.yml)

A lightweight Python TCP failover proxy for Minecraft. It forwards **new incoming connections** to either **MAIN** or **FALLBACK** depending on MAIN server reachability. Configuration is TOML-based (`config.toml`).

## Overview

- Players connect to the proxy listen port.
- The proxy continuously health-checks the MAIN server.
- If MAIN is reachable, new players are forwarded to MAIN.
- If MAIN is down/unreachable, new players are forwarded to FALLBACK (for example, a lobby/waiting server).
- Already connected players are **not** moved live.

> The proxy only decides on new TCP connections. Already connected players cannot be migrated live to FALLBACK automatically.

## Architecture (text diagram)

```text
Players
   |
   v
Minecraft Python Failover Proxy
   |------------------> MAIN
   |
   \------------------> FALLBACK
```

## Features

- TCP proxying for Minecraft traffic
- Fully configurable MAIN/FALLBACK targets
- TOML configuration (`config.toml`)
- Healthcheck mode `tcp` (recommended default)
- Optional healthcheck mode `minecraft_status`
- Failover threshold (`fail_after`)
- Recovery threshold (`recover_after`)
- Configurable log level
- Linux/systemd-friendly deployment
- Python 3.10+
- Unit tests + GitHub Actions CI (3.10, 3.11, 3.12)

## Requirements

- Linux server/VPS recommended
- Python 3.10+
- Open firewall port for proxy listen port (default `25565/tcp`)
- MAIN and FALLBACK must be reachable from the proxy host
- Python 3.10: install `tomli` via `requirements.txt`

## Installation

```bash
git clone https://github.com/leonardgrimm13-netizen/Minecraft-Python-Failover-Proxy.git
cd Minecraft-Python-Failover-Proxy
python3 -m pip install -r requirements.txt
```

Optional virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Notes:

- On Python 3.11+, `requirements.txt` typically adds no runtime dependency for TOML parsing (the interpreter uses built-in `tomllib`).
- On Python 3.10, `tomli` is required and installed from `requirements.txt`.

## Configuration (`config.toml`)

Full example:

```toml
[proxy]
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
```

| Key | Meaning | Typical value |
|---|---|---|
| `proxy.listen_host` | Listen interface/IP for incoming clients | `0.0.0.0` |
| `proxy.listen_port` | Listen TCP port for incoming clients | `25565` |
| `main.host` | MAIN server hostname/IP | `127.0.0.1` |
| `main.port` | MAIN server TCP port | `25564` |
| `fallback.host` | FALLBACK hostname/IP | `127.0.0.1` |
| `fallback.port` | FALLBACK TCP port | `25566` |
| `healthcheck.mode` | Healthcheck type: `tcp` or `minecraft_status` | `tcp` |
| `healthcheck.interval_seconds` | Seconds between health checks | `3.0` |
| `healthcheck.timeout_seconds` | Timeout per healthcheck attempt | `2.0` |
| `healthcheck.fail_after` | Consecutive failures before switch to FALLBACK | `2` |
| `healthcheck.recover_after` | Consecutive successes before switch back to MAIN | `2` |
| `healthcheck.target_host` | Optional host for healthcheck target override | `100.64.0.10` |
| `healthcheck.target_port` | Optional port for healthcheck target override | `25567` |
| `healthcheck.protocol_version` | Status handshake protocol version (default) | `767` |
| `healthcheck.status_hostname` | Optional hostname sent in status handshake | `survival.example.com` |
| `healthcheck.require_valid_json` | Require valid JSON status response | `true` |
| `healthcheck.log_status_details` | Log version/players/latency on success | `false` |
| `healthcheck.jitter_seconds` | Random delay added per check to reduce synchronized bursts | `0.2` |
| `connection.timeout_seconds` | Upstream connection timeout | `5.0` |
| `connection.buffer_size` | TCP forwarding buffer size | `65536` |
| `connection.idle_timeout_seconds` | Idle timeout for established proxied connections (`0` = disabled) | `300.0` |
| `connection.connect_fallback_on_main_connect_failure` | If MAIN connect fails, try FALLBACK immediately | `true` |
| `connection.tcp_keepalive` | Enable SO_KEEPALIVE on proxied sockets | `true` |
| `connection.max_connections` | Hard limit for concurrent client sessions | `4096` |
| `logging.level` | Logging level (`DEBUG`, `INFO`, ...) | `INFO` |

Guidance:

- `healthcheck.mode = "tcp"` is the most robust default in mixed environments.
- `minecraft_status` can be more protocol-aware, but may be more sensitive depending on server version/proxies/network middleboxes.
- `fail_after` avoids immediate failover from a single transient failure.
- `recover_after` avoids flapping and early switchback to MAIN.
- Default behavior in code is conservative and backward-safe: `connect_fallback_on_main_connect_failure = false`, `tcp_keepalive = false`.
- The `config.example.toml` intentionally enables both (`true`) as recommended production defaults for new installs.
- `idle_timeout_seconds = 0` disables idle disconnects completely.

## Velocity / Backend healthcheck

When MAIN points to Velocity, a simple TCP check may only prove Velocity is listening, not that the real backend is reachable.

- `main.host` / `main.port` = routing target for players when MAIN is healthy.
- `healthcheck.target_host` / `healthcheck.target_port` = healthcheck target used only for deciding MAIN health.

Example (route to Velocity, check real backend):

The default example uses `mode = "tcp"` for safe first startup. For a Velocity setup where you want to verify the real backend behind Velocity, switch to `mode = "minecraft_status"` and set `target_host`/`target_port`.


```toml
[main]
host = "127.0.0.1"
port = 25564

[fallback]
host = "127.0.0.1"
port = 25566

[healthcheck]
mode = "minecraft_status"
target_host = "100.64.0.10"
target_port = 25567
protocol_version = 767
require_valid_json = true
log_status_details = false
interval_seconds = 3.0
timeout_seconds = 2.0
fail_after = 2
recover_after = 2
```

Notes:
- `protocol_version = 767` is the default and can be changed if your stack needs another protocol id.
- `require_valid_json = true` validates a real status JSON response. If `false`, only a valid status packet id is required.
- `log_status_details = true` logs successful version/player/latency details and can be noisy with short intervals.
- Backend server must allow status pings (`enable-status=true` in `server.properties`).
- `nc -vz` only proves TCP reachability; `minecraft_status` verifies Minecraft-like status behavior.

## Start

```bash
python3 mc_failover_proxy.py
python3 mc_failover_proxy.py --config /path/config.toml
```

- Default config path is `./config.toml` relative to the current working directory.
- For systemd, `WorkingDirectory` matters for predictable config resolution.

## systemd service example

```ini
[Unit]
Description=Minecraft Python Failover Proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/mc-failover
ExecStart=/usr/bin/python3 /opt/mc-failover/mc_failover_proxy.py --config /opt/mc-failover/config.toml
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mc-failover
systemctl status mc-failover
journalctl -u mc-failover -f
```

Note: `ProtectHome=true` can block config files stored under home directories. Prefer placing files under `/opt/mc-failover` or adjust hardening settings intentionally.

## Firewall

```bash
sudo ufw allow 25565/tcp
```

## Tests and checks

```bash
python3 -m unittest discover -s tests
python3 -m py_compile mc_failover_proxy.py
python3 -m compileall .
```

## Troubleshooting

- `config.toml` not found:
  - Check `WorkingDirectory` and `--config` path.
  - Start explicitly: `python3 mc_failover_proxy.py --config config.toml`
- Invalid TOML:
  - Validate section syntax, quotes, and key/value formatting.
- Wrong types in config:
  - Ports must be integers, timeout/interval values numeric.
- Proxy loop detected:
  - Do not point MAIN/FALLBACK to the proxy listener host+port.
- Port already in use:
  - Check listeners: `ss -ltnp | grep 25565`
- MAIN unreachable:
  - Verify routing/firewall/DNS from proxy host to MAIN.
- FALLBACK unreachable:
  - Ensure fallback target is actually reachable when needed.
- Python 3.10 without `tomli`:
  - Run: `python3 -m pip install -r requirements.txt`
- systemd cannot find config:
  - Use absolute `--config` path and set `WorkingDirectory` explicitly.
- Players connecting to wrong port:
  - Ensure DNS/SRV record and client target point to proxy listen port.
- Velocity/HAProxy already on 25565:
  - Move one listener to another port, or re-architect chain carefully.
- Tailscale/WireGuard routing issues:
  - Verify route advertisements, ACLs, and allow traffic between subnets/hosts.
- Config load errors in stderr:
  - Current implementation prints `Konfigurationsfehler: ...` to stderr on config failures and exits with code `1`.

## Project limits (important)

- Not a full Minecraft proxy replacement like Velocity.
- No live migration for already connected players.
- No login/packet rewriting logic.
- No multi-main load-balancing.
- Healthcheck result influences only new incoming connections.

## Security notes

- Do not run as root unless truly necessary.
- Keep firewall rules minimal/restrictive.
- Bind only to required interfaces.
- `config.toml` contains no secrets by design.
- Port `25565` is above `1024`, so root is usually not required.

## Example deployment patterns

1. **Single host (local MAIN + local FALLBACK)**
   - MAIN: `127.0.0.1:25567`
   - FALLBACK: `127.0.0.1:25566`
   - Proxy: `0.0.0.0:25565`

2. **MAIN over Tailscale/VPN, FALLBACK local**
   - MAIN: private VPN IP/hostname
   - FALLBACK: local waiting server
   - Useful when primary game server is remote.

3. **Proxy on VPS, home MAIN via Tailscale**
   - Public entrypoint on VPS
   - MAIN at home reachable via Tailscale
   - FALLBACK either on VPS or another reachable host.

---

[Deutsch](README.de.md)


## Advanced Minecraft Status Checks

```toml
[healthcheck]
mode = "minecraft_status"
target_host = "100.64.0.10"
target_port = 25567
require_valid_json = true
expected_version_contains = "1.21"
motd_must_contain = "READY"
motd_must_not_contain = "STARTING"
max_latency_ms = 1500
min_players_max = 1
```

- Status ping requires `enable-status=true` in `server.properties`.
- In Velocity/Paper setups, ensure the intended backend answers the status ping.
- Practical tip: set MOTD to `STARTING` during boot and `READY` when fully loaded; then use `motd_must_contain = "READY"`.
- Warning: MOTD filtering depends on server configuration and does not replace plugin-level readiness signaling.
- Matching for `expected_version_contains`, `motd_must_contain`, and `motd_must_not_contain` is case-sensitive.
