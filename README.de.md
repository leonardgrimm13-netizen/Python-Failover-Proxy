# Minecraft Python Failover Proxy

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Plattform: Linux](https://img.shields.io/badge/Platform-Linux-informational.svg)](https://kernel.org/)
[![Tests](https://github.com/leonardgrimm13-netizen/Minecraft-Python-Failover-Proxy/actions/workflows/tests.yml/badge.svg)](https://github.com/leonardgrimm13-netizen/Minecraft-Python-Failover-Proxy/actions/workflows/tests.yml)

Ein leichter Python-TCP-Failover-Proxy für Minecraft. Er leitet **neue Verbindungen** abhängig von der Erreichbarkeit des Hauptservers entweder an **MAIN** oder **FALLBACK** weiter. Die Konfiguration erfolgt per TOML (`config.toml`).

## Überblick

- Spieler verbinden sich auf den Listen-Port des Proxys.
- Der Proxy prüft den MAIN-Server in festen Intervallen.
- Ist MAIN erreichbar, werden neue Spieler an MAIN weitergeleitet.
- Ist MAIN nicht erreichbar, werden neue Spieler an FALLBACK (z. B. Lobby/Warteraum) weitergeleitet.
- Bereits verbundene Spieler werden **nicht** live umgezogen.

> Der Proxy entscheidet nur bei neuen TCP-Verbindungen. Bereits verbundene Spieler können nicht automatisch live auf den Fallback-Server migriert werden.

## Architektur (Textgrafik)

```text
Spieler
   |
   v
Minecraft Python Failover Proxy
   |------------------> MAIN / Hauptserver
   |
   \------------------> FALLBACK / Warteraum
```

## Funktionen

- TCP-Proxy für Minecraft-Traffic
- MAIN/FALLBACK frei konfigurierbar
- TOML-Konfiguration (`config.toml`)
- Healthcheck-Modus `tcp` (empfohlener Standard)
- Optionaler Modus `minecraft_status`
- Failover-Schwelle `fail_after`
- Recovery-Schwelle `recover_after`
- Konfigurierbares Logging-Level
- systemd-tauglicher Betrieb auf Linux
- Python 3.10+
- Unit-Tests + GitHub Actions (3.10, 3.11, 3.12)

## Voraussetzungen

- Linux-Server/VPS empfohlen
- Python 3.10+
- Offener Firewall-Port für den Proxy (standardmäßig `25565/tcp`)
- MAIN und FALLBACK müssen vom Proxy-Host aus erreichbar sein
- Bei Python 3.10: `tomli` über `requirements.txt` installieren

## Installation

```bash
git clone https://github.com/leonardgrimm13-netizen/Minecraft-Python-Failover-Proxy.git
cd Minecraft-Python-Failover-Proxy
python3 -m pip install -r requirements.txt
```

Optional mit virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Hinweise:

- Unter Python 3.11+ bringt `requirements.txt` für TOML in der Regel keine zusätzliche Runtime-Abhängigkeit (es wird `tomllib` aus der Standardbibliothek genutzt).
- Unter Python 3.10 wird `tomli` benötigt und über `requirements.txt` installiert.

## Konfiguration (`config.toml`)

Vollständiges Beispiel:

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

| Option | Bedeutung | Typischer Wert |
|---|---|---|
| `proxy.listen_host` | Interface/IP, auf dem der Proxy lauscht | `0.0.0.0` |
| `proxy.listen_port` | TCP-Port für eingehende Spieler | `25565` |
| `main.host` | Hostname/IP des Hauptservers (MAIN) | `127.0.0.1` |
| `main.port` | TCP-Port des Hauptservers | `25564` |
| `fallback.host` | Hostname/IP des Fallback-Servers | `127.0.0.1` |
| `fallback.port` | TCP-Port des Fallback-Servers | `25566` |
| `healthcheck.mode` | Healthcheck-Typ: `tcp` oder `minecraft_status` | `tcp` |
| `healthcheck.interval_seconds` | Intervall zwischen Prüfungen (Sekunden) | `3.0` |
| `healthcheck.timeout_seconds` | Timeout pro Prüfvorgang (Sekunden) | `2.0` |
| `healthcheck.fail_after` | Anzahl Fehlversuche bis Umschaltung auf FALLBACK | `2` |
| `healthcheck.recover_after` | Anzahl Erfolge bis Rückschaltung auf MAIN | `2` |
| `healthcheck.target_host` | Optionales Ziel für den Healthcheck-Host | `100.64.0.10` |
| `healthcheck.target_port` | Optionales Ziel für den Healthcheck-Port | `25567` |
| `healthcheck.protocol_version` | Protokollversion im Status-Handshake (Default) | `767` |
| `healthcheck.status_hostname` | Optionaler Hostname im Status-Handshake | `survival.example.com` |
| `healthcheck.require_valid_json` | Gültige JSON-Statusantwort erzwingen | `true` |
| `healthcheck.log_status_details` | Erfolgreiche Version/Spieler/Latenz loggen | `false` |
| `healthcheck.jitter_seconds` | Zufälliger Zusatz-Delay pro Check gegen gleichzeitige Bursts | `0.2` |
| `connection.timeout_seconds` | Timeout für Upstream-Verbindungsaufbau | `5.0` |
| `connection.buffer_size` | Puffergröße für TCP-Weiterleitung | `65536` |
| `connection.idle_timeout_seconds` | Idle-Timeout für bestehende Proxy-Verbindungen (`0` = deaktiviert) | `300.0` |
| `connection.connect_fallback_on_main_connect_failure` | Bei MAIN-Connect-Fehler sofort FALLBACK versuchen | `true` |
| `connection.tcp_keepalive` | Aktiviert SO_KEEPALIVE auf Proxy-Sockets | `true` |
| `connection.max_connections` | Hartes Limit für gleichzeitige Verbindungen | `4096` |
| `logging.level` | Logging-Level (`DEBUG`, `INFO`, ...) | `INFO` |

Wichtige Einordnung:

- `healthcheck.mode = "tcp"` ist der stabilste Standard in gemischten Umgebungen.
- `minecraft_status` ist protokollnäher, kann aber je nach Server/Proxy/Version empfindlicher reagieren.
- `fail_after` verhindert sofortiges Umschalten bei einzelnen Kurzstörungen.
- `recover_after` verhindert zu frühes Zurückschalten und reduziert Flapping.
- Code-Defaults sind bewusst konservativ/rückwärtskompatibel: `connect_fallback_on_main_connect_failure = false`, `tcp_keepalive = false`.
- In `config.example.toml` sind beide bewusst als empfohlene Produktionswerte auf `true` gesetzt.
- `idle_timeout_seconds = 0` deaktiviert den Idle-Disconnect vollständig.

## Velocity / Backend-Healthcheck

Wenn MAIN auf Velocity zeigt, beweist ein reiner TCP-Check oft nur, dass Velocity läuft – nicht, dass das eigentliche Backend erreichbar ist.

- `main.host` / `main.port` = Routing-Ziel für Spieler bei gesundem MAIN.
- `healthcheck.target_host` / `healthcheck.target_port` = separates Prüfziel für die Gesundheitsentscheidung.

Beispiel (zu Velocity routen, echtes Backend prüfen):

Das Standardbeispiel nutzt `mode = "tcp"` für einen sicheren Erststart. Für ein Velocity-Setup, bei dem der echte Backend-Server hinter Velocity geprüft werden soll, stelle auf `mode = "minecraft_status"` um und setze `target_host`/`target_port`.


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

Hinweise:
- `protocol_version = 767` ist der Default und kann bei Bedarf angepasst werden.
- `require_valid_json = true` erzwingt eine echte JSON-Statusantwort. Bei `false` reicht ein gültiger Status-Pakettyp.
- `log_status_details = true` protokolliert Version/Spieler/Latenz und kann bei kurzem Intervall viel Log erzeugen.
- Backend muss Status-Pings erlauben (`enable-status=true` in `server.properties`).
- `nc -vz` zeigt nur TCP-Erreichbarkeit; `minecraft_status` prüft Minecraft-typisches Statusverhalten.

## Start

```bash
python3 mc_failover_proxy.py
python3 mc_failover_proxy.py --config /pfad/config.toml
```

- Standardpfad ist `./config.toml` im aktuellen Working Directory.
- Für systemd ist `WorkingDirectory` deshalb wichtig.

## systemd-Service (Beispiel)

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

Hinweis: `ProtectHome=true` kann problematisch sein, wenn die Config im Home-Verzeichnis liegt. In der Praxis ist ein Pfad wie `/opt/mc-failover` meist besser, alternativ systemd-Sandboxing bewusst anpassen.

## Firewall

```bash
sudo ufw allow 25565/tcp
```

## Monitoring

Optionaler integrierter HTTP-Monitoring-Port (standardmäßig deaktiviert).

```toml
[monitoring]
enabled = false
listen_host = "127.0.0.1"
listen_port = 8080
allow_remote = false
```

Beispiele:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/ready
curl http://127.0.0.1:8080/state
curl http://127.0.0.1:8080/metrics
```

- Standard: deaktiviert.
- Standard: nur localhost.
- Für Uptime Kuma: `/health` oder `/ready`.
- Für Prometheus: `/metrics`.
- Sicherheit: Monitoring-Port nicht direkt öffentlich ins Internet öffnen; besser über Tailscale, WireGuard, SSH-Tunnel oder Reverse-Proxy mit Auth.
- Wenn du auf einem VPS arbeitest, lasse `listen_host` am besten auf `127.0.0.1` und greife per SSH-Tunnel oder Tailscale darauf zu.

## Tests und Checks

```bash
python3 -m unittest discover -s tests
python3 -m py_compile mc_failover_proxy.py
python3 -m compileall .
```

## Fehlersuche (Troubleshooting)

- `config.toml` nicht gefunden:
  - `WorkingDirectory` und `--config`-Pfad prüfen.
  - Explizit starten: `python3 mc_failover_proxy.py --config config.toml`
- Ungültiges TOML:
  - TOML-Syntax (Sections, Quotes, Key/Value-Format) korrigieren.
- Falsche Typen in der Config:
  - Ports müssen Integer sein, Timeout/Intervall numerisch.
- Proxy-Schleife erkannt:
  - MAIN/FALLBACK dürfen nicht auf denselben Listener-Host+Port zeigen.
- Port bereits belegt:
  - Prüfen mit: `ss -ltnp | grep 25565`
- MAIN nicht erreichbar:
  - Routing, Firewall, DNS vom Proxy-Host aus kontrollieren.
- FALLBACK nicht erreichbar:
  - Erreichbarkeit des Fallback-Servers gezielt testen.
- Python 3.10 ohne `tomli`:
  - `python3 -m pip install -r requirements.txt`
- systemd findet Config nicht:
  - Absoluten `--config`-Pfad + passendes `WorkingDirectory` setzen.
- Spieler verbinden sich auf falschen Port:
  - DNS/SRV-Einträge und Client-Ziel auf Proxy-Port prüfen.
- Velocity/HAProxy nutzt bereits Port 25565:
  - Einen Dienst auf anderen Port verschieben oder Kette sauber neu planen.
- Tailscale-/WireGuard-Routing-Probleme:
  - Routen, ACLs und Freigaben zwischen Netzen/Hosts prüfen.
- Konfigurationsfehler auf stderr:
  - Aktuell gibt das Programm bei Config-Problemen `Konfigurationsfehler: ...` auf stderr aus und beendet sich mit Exit-Code `1`.

## Grenzen des Projekts

- Kein vollwertiger Minecraft-Proxy wie Velocity.
- Keine Live-Migration bereits verbundener Spieler.
- Kein Login-/Packet-Rewrite.
- Kein Load-Balancer für mehrere Hauptserver.
- Der Healthcheck beeinflusst nur neue Verbindungen.

## Sicherheit

- Nicht unnötig als root ausführen.
- Firewall möglichst restriktiv halten.
- Nur notwendige Interfaces binden.
- `config.toml` enthält bewusst keine Secrets.
- Für Port `25565` ist root i. d. R. nicht nötig (Port > 1024).

## Beispiel-Setups

1. **MAIN und FALLBACK lokal auf demselben Host**
   - MAIN: `127.0.0.1:25567`
   - FALLBACK: `127.0.0.1:25566`
   - Proxy: `0.0.0.0:25565`

2. **MAIN über Tailscale/VPN, FALLBACK lokal**
   - MAIN auf privater VPN-IP/Hostname
   - FALLBACK als lokale Lobby/Warteraum
   - Sinnvoll, wenn der primäre Server extern betrieben wird.

3. **Proxy auf VPS, MAIN zuhause via Tailscale**
   - Öffentlicher Einstiegspunkt auf VPS
   - MAIN-Server zuhause über Tailscale erreichbar
   - FALLBACK auf VPS oder anderem erreichbaren Host.

---

[English](README.md)
