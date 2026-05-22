# Minecraft Python Failover Proxy

Leichter Python-TCP-Proxy für Minecraft mit automatischer Weiterleitung auf einen Fallback-Server.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Lizenz](https://img.shields.io/badge/License-MIT-green.svg)
![Plattform](https://img.shields.io/badge/Platform-Linux-informational)

## Überblick

Der **Minecraft Python Failover Proxy** nimmt Verbindungen auf einem festen **Proxy-Port** an und entscheidet anhand eines regelmäßigen **Healthchecks**, wohin **neue Verbindungen** gehen:

- Ist der **Hauptserver** (bzw. **Standardserver**) erreichbar, leitet der Proxy auf den Hauptserver weiter.
- Ist der Hauptserver nicht erreichbar, leitet der Proxy auf den **Fallback-Server** bzw. **Warteraum-Server** weiter.

> Wichtig: Das Skript entscheidet nur bei **neuen Verbindungen**. Bereits verbundene Spieler können nicht live auf den Fallback-Server verschoben werden, wenn der Hauptserver abstürzt.

## Architektur

```text
Spieler
   |
   v
Python Failover Proxy
   |------------------> Hauptserver / Standardserver (gesund)
   |
   \------------------> Fallback-Server / Warteraum-Server (Hauptserver nicht erreichbar)
```

## Funktionen

- TCP-Proxy für Minecraft-Verbindungen
- Hauptserver und Fallback-Server frei konfigurierbar
- Periodischer Healthcheck
- Umschalten auf Fallback erst nach definierter Fehleranzahl (`FAIL_AFTER`)
- Rückschalten auf Hauptserver erst nach definierter Erfolgsanzahl (`RECOVER_AFTER`)
- Optionaler Minecraft-Status-Ping als Healthcheck (`HEALTH_CHECK_MODE = "minecraft_status"`)
- systemd-freundlich
- Keine externen Python-Abhängigkeiten (nur Standardbibliothek)

## Voraussetzungen

- Linux-Server/VPS empfohlen
- Python 3.10+
- Offener Firewall-Port für den Proxy-Port
- Hauptserver und Fallback-Server müssen vom Proxy-Host aus erreichbar sein

## Installation

```bash
git clone https://github.com/leonardgrimm13-netizen/Minecraft-Python-Failover-Proxy.git
cd Minecraft-Python-Failover-Proxy
```

Optional (virtuelle Umgebung):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Es ist standardmäßig kein `pip install` nötig, da nur die Python-Standardbibliothek verwendet wird.

## Konfiguration

Die Konfiguration erfolgt direkt oben in `mc_failover_proxy.py`:

| Variable | Bedeutung | Beispiel |
|---|---|---|
| `LISTEN_HOST` | Host/IP, auf dem der Proxy lauscht | `"0.0.0.0"` |
| `LISTEN_PORT` | Externer Proxy-Port für Spieler | `25565` |
| `MAIN_HOST` | Host/IP vom Hauptserver | `"100.80.12.34"` |
| `MAIN_PORT` | Port vom Hauptserver | `25565` |
| `FALLBACK_HOST` | Host/IP vom Fallback-Server/Warteraum-Server | `"127.0.0.1"` |
| `FALLBACK_PORT` | Port vom Fallback-Server | `25566` |
| `CHECK_INTERVAL_SECONDS` | Zeit zwischen Healthchecks | `3.0` |
| `CHECK_TIMEOUT_SECONDS` | Timeout pro Healthcheck | `2.0` |
| `FAIL_AFTER` | Anzahl Fehlversuche bis zum Umschalten auf Fallback | `2` |
| `RECOVER_AFTER` | Anzahl erfolgreicher Checks bis zurück zum Hauptserver | `2` |
| `HEALTH_CHECK_MODE` | Modus: `"tcp"` oder `"minecraft_status"` | `"tcp"` |
| `LOG_LEVEL` | Logging-Level (`"INFO"`, `"DEBUG"`, ...) | `"INFO"` |

## Beispiel-Setup

- Proxy lauscht auf `0.0.0.0:25565`
- Hauptserver auf `100.x.x.x:25565`
- Fallback-Server/Warteraum-Server auf `127.0.0.1:25566`

## Manueller Start

```bash
python3 mc_failover_proxy.py
```

## Tests

```bash
python3 -m unittest
```

## Betrieb als systemd-Service

Beispieldatei: `/etc/systemd/system/mc-failover.service`

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

Befehle:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mc-failover
systemctl status mc-failover
journalctl -u mc-failover -f
```

## Firewall-Beispiel

```bash
sudo ufw allow 25565/tcp
```

## Fehlersuche

- **Port bereits belegt**: Mit `ss -ltnp | grep 25565` prüfen, was den Port nutzt.
- **Hauptserver nicht erreichbar**: `MAIN_HOST`/`MAIN_PORT`, Routing und Firewall prüfen.
- **Fallback-Server nicht erreichbar**: Fallback-Host, Port und lokale Bind-Settings prüfen.
- **Spieler verbinden sich auf den falschen Port**: DNS/SRV/Direct-Connect auf den Proxy-Port zeigen lassen.
- **Velocity/HAProxy nutzt bereits 25565**: Ports trennen und Weiterleitung sauber aufsetzen.
- **Tailscale/WireGuard-Route nicht erreichbar**: Overlay-Routen und ACL/Firewall prüfen.

## Grenzen des Projekts

- Kein vollständiger Minecraft-Netzwerkproxy wie Velocity
- Keine Live-Migration bereits verbundener Spieler
- Ersetzt keine Velocity-Fallback-Logik
- Der Healthcheck entscheidet nur über neue Verbindungen

## Sicherheit

- Nicht als Root ausführen, außer wenn für privilegierte Ports erforderlich.
- Firewall-Regeln restriktiv halten.
- Nur auf die benötigten Interfaces binden.

## Roadmap

- Unterstützung für `config.toml` oder `config.yaml`
- Dockerfile
- GitHub-Actions-Tests
- PROXY-Protocol-Support
- Prometheus-Metriken
- Graceful Draining

## Lizenz

Dieses Projekt steht unter der MIT-Lizenz. Siehe [LICENSE](LICENSE).

## Sprache

[English](README.md)
