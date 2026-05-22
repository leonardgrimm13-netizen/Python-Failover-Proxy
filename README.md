# Minecraft Python Failover Proxy

Lightweight Python TCP failover proxy for Minecraft with TOML-based config.

## Quick Start

```bash
git clone https://github.com/leonardgrimm13-netizen/Minecraft-Python-Failover-Proxy.git
cd Minecraft-Python-Failover-Proxy
python3 -m pip install -r requirements.txt
python3 mc_failover_proxy.py
```

- Python 3.11+ uses built-in `tomllib`.
- Python 3.10 requires `tomli` from `requirements.txt`.

## Configuration (`config.toml`)

Edit `config.toml`:

```toml
[proxy]
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
```

You can also start with a custom config path:

```bash
python3 mc_failover_proxy.py --config /path/config.toml
```

Default config path is `./config.toml` (current working directory).

## systemd Example

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

[Install]
WantedBy=multi-user.target
```

## Troubleshooting

- `config.toml` not found: check `WorkingDirectory` or `--config` path.
- Invalid TOML: fix syntax (sections, quotes, commas).
- Wrong value types: ports must be ints, timeouts must be numeric.
- Proxy loop detected: do not point MAIN/FALLBACK to listener host+port.
- Python 3.10 import error for TOML: run `python3 -m pip install -r requirements.txt`.
