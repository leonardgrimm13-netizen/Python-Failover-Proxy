# Minecraft Python Failover Proxy

Leichter Python-TCP-Failover-Proxy für Minecraft mit TOML-Konfiguration.

## Schnellstart

```bash
git clone https://github.com/leonardgrimm13-netizen/Minecraft-Python-Failover-Proxy.git
cd Minecraft-Python-Failover-Proxy
python3 -m pip install -r requirements.txt
python3 mc_failover_proxy.py
```

- Python 3.11+ nutzt `tomllib` aus der Standardbibliothek.
- Python 3.10 benötigt `tomli` aus `requirements.txt`.

## Konfiguration (`config.toml`)

Konfiguration in `config.toml` bearbeiten (siehe Datei im Repo-Root).

Start mit eigener Config-Datei:

```bash
python3 mc_failover_proxy.py --config /pfad/config.toml
```

Standardpfad ist `./config.toml` im aktuellen Working Directory.

## systemd-Beispiel

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

## Fehlersuche

- `config.toml` nicht gefunden: `WorkingDirectory` oder `--config` prüfen.
- Ungültiges TOML: Syntaxfehler in der Datei beheben.
- Falsche Typen: Ports = Integer, Timeouts = numerisch.
- Proxy-Schleife erkannt: MAIN/FALLBACK nicht auf Listener-Host+Port zeigen lassen.
- Python 3.10 ohne `tomli`: `python3 -m pip install -r requirements.txt`.
