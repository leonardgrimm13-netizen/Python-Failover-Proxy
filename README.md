# Minecraft Python Failover Proxy

Lightweight Python TCP failover routing for Minecraft server entrypoints.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Platform](https://img.shields.io/badge/Platform-Linux-informational)

## Overview

**Minecraft Python Failover Proxy** is a small TCP proxy that routes **new** incoming Minecraft connections based on the health of your main server.

- If the main server is reachable, new players are forwarded to the main server.
- If the main server is unreachable, new players are forwarded to a fallback/waiting-room server.

> Important: This proxy only decides the target for **new connections**. Already connected players cannot be live-migrated automatically if the main server crashes.

## Architecture

```text
Players
   |
   v
Python Failover Proxy
   |------------------> Main Server (healthy)
   |
   \------------------> Fallback Server (main unhealthy)
```

## Features

- TCP proxy for Minecraft connections
- Configurable main and fallback target
- Periodic health checks
- Failover threshold (`FAIL_AFTER`)
- Recovery threshold (`RECOVER_AFTER`)
- Optional Minecraft status-ping health check mode (`HEALTH_CHECK_MODE = "minecraft_status"`)
- systemd-friendly (single process, clean logs, restart support)
- No external Python dependencies (standard library only)

## Requirements

- Linux server/VPS recommended
- Python 3.10+
- Firewall port open for the proxy listener
- Main server and fallback server reachable from the proxy host

## Installation

```bash
git clone https://github.com/leonardgrimm13-netizen/Python-Failover-Proxy.git
cd Python-Failover-Proxy
```

Optional virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

No `pip install` is required by default because this project uses only the Python standard library.

Start directly:

```bash
python3 mc_failover_proxy.py
```

## Configuration

Edit the variables at the top of `mc_failover_proxy.py`:

| Variable | Meaning | Example |
|---|---|---|
| `LISTEN_HOST` | Interface/IP where the proxy listens | `"0.0.0.0"` |
| `LISTEN_PORT` | TCP port exposed to players | `25565` |
| `MAIN_HOST` | Main Minecraft server hostname/IP | `"100.80.12.34"` |
| `MAIN_PORT` | Main server port | `25565` |
| `FALLBACK_HOST` | Fallback/waiting-room server hostname/IP | `"127.0.0.1"` |
| `FALLBACK_PORT` | Fallback server port | `25566` |
| `CHECK_INTERVAL_SECONDS` | Interval between health checks | `3.0` |
| `CHECK_TIMEOUT_SECONDS` | Health check timeout per check | `2.0` |
| `FAIL_AFTER` | Consecutive failures required before failover | `2` |
| `RECOVER_AFTER` | Consecutive successes required before recovery to main | `2` |
| `HEALTH_CHECK_MODE` | Health check mode (`"tcp"` or `"minecraft_status"`) | `"tcp"` |

## Example Setup

- Proxy listens on `0.0.0.0:25565`
- Main server at `100.x.x.x:25565`
- Fallback server at `127.0.0.1:25566`

## Running Manually

```bash
python3 mc_failover_proxy.py
```

## Running as a systemd Service

Example file: `/etc/systemd/system/mc-failover.service`

```ini
[Unit]
Description=Minecraft Python Failover Proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/mc-failover
ExecStart=/usr/bin/python3 /opt/mc-failover/mc_failover_proxy.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

Commands:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mc-failover
systemctl status mc-failover
journalctl -u mc-failover -f
```

## Firewall Example

```bash
sudo ufw allow 25565/tcp
```

## Troubleshooting

- **Port already in use**: Check with `ss -ltnp | grep 25565` and stop conflicting services.
- **Main server not reachable**: Verify `MAIN_HOST`, `MAIN_PORT`, routing, and firewall rules.
- **Fallback server not reachable**: Verify fallback host/port and local bind settings.
- **Players connect to wrong port**: Ensure DNS/SRV/direct connect points to the proxy port.
- **Velocity/HAProxy already uses port 25565**: Move one service to another port and forward correctly.
- **Tailscale/WireGuard route not reachable**: Validate overlay-network routes and ACL/firewall rules.

## Limitations

- Not a full Minecraft proxy platform like Velocity
- No live migration of already connected players
- Does not replace Velocity fallback logic
- Health checks only influence routing for new connections

## Recommended Usage

- Place this proxy in front of a normal Minecraft server and a limbo/waiting-room server.
- For advanced Minecraft network topologies, Velocity fallback features may be a better fit.

## Security Notes

- Do not run as root unless you specifically need a privileged port.
- Use restrictive firewall rules.
- Bind only to the interface(s) you actually need.

## Roadmap

- `config.toml` or `config.yaml` support
- Dockerfile
- GitHub Actions tests
- PROXY protocol support
- Prometheus metrics
- Graceful draining

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

## Language

[Deutsch](README.de.md)
