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
min_recovery_seconds = 0.0

[connection]
timeout_seconds = 5.0
buffer_size = 65536

[maintenance]
mode = "auto"
force_fallback_file = "/var/lib/mc-failover/force_fallback"
force_main_file = "/var/lib/mc-failover/force_main"

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
| `healthcheck.min_recovery_seconds` | Zusätzliche stabile Healthy-Zeit vor Rückschaltung (`0.0` = deaktiviert) | `30.0` |
| `healthcheck.target_host` | Optionales Ziel für den Healthcheck-Host | `100.64.0.10` |
| `healthcheck.target_port` | Optionales Ziel für den Healthcheck-Port | `25567` |
| `healthcheck.protocol_version` | Protokollversion im Status-Handshake (Default) | `767` |
| `healthcheck.status_hostname` | Optionaler Hostname im Status-Handshake | `survival.example.com` |
| `healthcheck.require_valid_json` | Gültige JSON-Statusantwort erzwingen | `true` |
| `healthcheck.log_status_details` | Erfolgreiche Version/Spieler/Latenz loggen | `false` |
| `healthcheck.jitter_seconds` | Zufälliger Zusatz-Delay pro Check gegen gleichzeitige Bursts | `0.2` |
| `healthcheck.max_latency_ms` | Maximale erlaubte Minecraft-Status-Latenz in ms (`0.0` = deaktiviert, funktioniert auch ohne JSON-Parsing) | `1500` |
| `healthcheck.expected_version_contains` | Erforderlicher Textausschnitt in `version.name` beim `minecraft_status`-JSON (`""` = deaktiviert) | `1.21` |
| `healthcheck.motd_must_contain` | Erforderlicher case-sensitiver Text in der MOTD (`""` = deaktiviert) | `READY` |
| `healthcheck.motd_must_not_contain` | Verbotener case-sensitiver Text in der MOTD (`""` = deaktiviert) | `STARTING` |
| `healthcheck.min_players_max` | Minimal erforderlicher `players.max`-Wert (`0` = deaktiviert) | `1` |
| `connection.timeout_seconds` | Timeout für Upstream-Verbindungsaufbau | `5.0` |
| `connection.buffer_size` | Puffergröße für TCP-Weiterleitung | `65536` |
| `connection.idle_timeout_seconds` | Idle-Timeout für bestehende Proxy-Verbindungen (`0` = deaktiviert) | `300.0` |
| `connection.connect_fallback_on_main_connect_failure` | Bei MAIN-Connect-Fehler sofort FALLBACK versuchen | `true` |
| `connection.tcp_keepalive` | Aktiviert SO_KEEPALIVE auf Proxy-Sockets | `true` |
| `connection.max_connections` | Hartes Limit für gleichzeitige Verbindungen | `4096` |
| `maintenance.mode` | Routing-Modus: `auto`, `force_fallback`, `force_main` | `auto` |
| `maintenance.force_fallback_file` | Existiert Datei, werden neue Spieler auf FALLBACK geleitet (ohne Neustart) | `/var/lib/mc-failover/force_fallback` |
| `maintenance.force_main_file` | Existiert Datei, werden neue Spieler auf MAIN geleitet (ohne Neustart) | `/var/lib/mc-failover/force_main` |
| `logging.level` | Logging-Level (`DEBUG`, `INFO`, ...) | `INFO` |

Wichtige Einordnung:

- `healthcheck.mode = "tcp"` ist der stabilste Standard in gemischten Umgebungen.
- `minecraft_status` ist protokollnäher, kann aber je nach Server/Proxy/Version empfindlicher reagieren.
- `fail_after` verhindert sofortiges Umschalten bei einzelnen Kurzstörungen.
- `recover_after` verhindert zu frühes Zurückschalten und reduziert Flapping.
- Code-Defaults sind bewusst konservativ/rückwärtskompatibel: `connect_fallback_on_main_connect_failure = false`, `tcp_keepalive = false`.
- In `config.example.toml` sind beide bewusst als empfohlene Produktionswerte auf `true` gesetzt.
- `idle_timeout_seconds = 0` deaktiviert den Idle-Disconnect vollständig.
- `force_fallback_file` hat Vorrang vor `force_main_file`, wenn beide Dateien existieren.
- Textfilter sind case-sensitive.
- JSON-basierte Filter brauchen `require_valid_json = true`.
- `max_latency_ms` funktioniert auch bei `require_valid_json = false`.


## Recovery-Wartezeit nach MAIN-Rückkehr

Minecraft-Server (Paper/Spigot/Velocity mit Plugins, Modpacks, Datenbanken) können TCP/Status oft schon beantworten, obwohl intern noch geladen wird.

- `recover_after`: wie viele erfolgreiche Checks am Stück nötig sind.
- `min_recovery_seconds`: zusätzliche durchgehende Healthy-Zeit nach der ersten erfolgreichen Antwort.

Für `FALLBACK -> MAIN` müssen beide Bedingungen erfüllt sein. Mit `min_recovery_seconds = 0.0` bleibt das alte Verhalten erhalten.

Beispiel:

```toml
[healthcheck]
interval_seconds = 3.0
fail_after = 2
recover_after = 3
min_recovery_seconds = 30.0
```

Bedeutung: Ausfall wird nach ca. 6 Sekunden erkannt; Rückschaltung erst nach 3 Erfolgs-Checks **und** mindestens 30 Sekunden stabiler Erreichbarkeit.
Bei großen Modpacks oder Paper-Servern mit vielen Plugins sind 20-60 Sekunden oft sinnvoll.

## Wartungsmodus / Force-Fallback

- `maintenance.mode = "auto"`: normales Verhalten, Healthcheck entscheidet.
- `maintenance.mode = "force_fallback"`: neue Spieler landen immer im FALLBACK/Warteraum.
- `maintenance.mode = "force_main"`: neue Spieler landen immer auf MAIN.
- Statischer Modus (`force_fallback`/`force_main`) hat Vorrang vor Datei-Overrides.
- In `auto` werden Datei-Overrides bei jeder neuen Verbindung neu geprüft (wirkt ohne Neustart).

Typische Admin-Befehle:

```bash
sudo mkdir -p /var/lib/mc-failover
sudo touch /var/lib/mc-failover/force_fallback
sudo rm /var/lib/mc-failover/force_fallback
```

Warnung: `force_main` kann Spieler auf MAIN leiten, obwohl der Healthcheck MAIN als unhealthy bewertet. Nur bewusst einsetzen.

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

## Erweiterter Minecraft-Status-Check

Paper/Velocity/Modpacks können bereits auf Status-Pings antworten, obwohl Plugins, Welten oder Datenbanken noch laden.
Mit den optionalen `minecraft_status`-Filtern kannst du die Readiness deutlich zuverlässiger prüfen.

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

- `enable-status=true` in `server.properties` ist nötig.
- Bei Velocity/Paper prüfen, welches Ziel den Status-Ping wirklich beantwortet.
- Textfilter sind case-sensitive.
- JSON-basierte Filter (`expected_version_contains`, `motd_*`, `min_players_max`) brauchen `require_valid_json = true`.
- `max_latency_ms` funktioniert auch bei `require_valid_json = false`.
- READY-MOTD-Praxis: während Start/Restart `STARTING`, nach vollständigem Start `READY`.
- MOTD-Filter sind nur ein Konfigurationssignal und ersetzen keinen echten Plugin-Readiness-Check.

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

## Docker

Schnellstart:

```bash
cp config.example.toml config.toml
nano config.toml
docker compose up -d
docker compose logs -f mc-failover
```

Hinweise:

- Port `25565/tcp` muss auf dem Host frei sein.
- Wenn ein anderer Dienst bereits `25565` nutzt, ändere das Port-Mapping in `docker-compose.yml`.
- MAIN und FALLBACK müssen aus dem Container erreichbar sein.
- Auf Linux kannst du für Host-Zugriffe `host.docker.internal` via `host-gateway` aktivieren (Beispiel ist in `docker-compose.yml` auskommentiert enthalten).


Wenn MAIN/FALLBACK auf demselben Linux-Host wie Docker laufen, aktiviere in `docker-compose.yml` das Host-Gateway-Mapping:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

Verwende dann den Host in `config.toml` für die Upstreams:

```toml
[main]
host = "host.docker.internal"
port = 25564

[fallback]
host = "host.docker.internal"
port = 25566
```

## systemd

Für einen produktionsnahen systemd-Betrieb nutze:

- Service-Unit: `packaging/systemd/mc-failover.service`
- Schritt-für-Schritt-Anleitung: `packaging/systemd/README.md`

Kurzbefehle:

```bash
sudo systemctl enable --now mc-failover
journalctl -u mc-failover -f
```

## Sicherheitshinweise

- Der Proxy muss nicht als root laufen (Container und systemd-Service nutzen dedizierte unprivilegierte User-Defaults).
- Lege die Laufzeit-Konfiguration außerhalb des Repos und außerhalb der Container-Image-Layer ab.
- Öffne in der Firewall nur die notwendigen Ports.
- Monitoring/Admin-Endpunkte nicht öffentlich exponieren, sofern nicht ausdrücklich erforderlich.

## Firewall

```bash
sudo ufw allow 25565/tcp
```

## Tests und Checks

```bash
python3 -m unittest discover -s tests
python3 -m py_compile mc_failover_proxy.py
python3 -m compileall .
```

## CLI-Checks für Admins (ohne Listener-Start)

Diese Befehle laden/validieren die Config und führen gezielte Tests aus, **ohne** den produktiven Proxy-Listener zu starten. Praktisch vor `systemctl enable/start` sowie für VPS-, Tailscale-, Velocity- und HAProxy-Debugging.

```bash
python3 mc_failover_proxy.py --config config.toml --check-config
python3 mc_failover_proxy.py --config config.toml --print-effective-config
python3 mc_failover_proxy.py --config config.toml --test-main
python3 mc_failover_proxy.py --config config.toml --test-fallback
python3 mc_failover_proxy.py --config config.toml --test-healthcheck
```

- `--test-main` prüft das reine TCP-Routing-Ziel von MAIN.
- `--test-fallback` prüft das reine TCP-Routing-Ziel von FALLBACK.
- `--test-healthcheck` prüft genau die konfigurierte Healthcheck-Entscheidung (`tcp` oder `minecraft_status`).
- Wenn `--test-main` erfolgreich ist, aber `--test-healthcheck` fehlschlägt, ist meistens Minecraft-Status-Ping (`enable-status`), `status_hostname` oder `protocol_version` die Ursache.

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

## Monitoring

Optional ist ein eingebauter HTTP-Monitoring-Port verfügbar (standardmäßig deaktiviert).

- Standard-Bind ist nur localhost (`127.0.0.1`).
- Der Endpoint sollte privat bleiben, da er interne Routing-/Health-Daten zeigt.

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/ready
curl http://127.0.0.1:8080/state
curl http://127.0.0.1:8080/metrics
```

Für Uptime Kuma nutze `/health` oder `/ready`, für Prometheus `/metrics`.
Monitoring-Port nicht direkt öffentlich ins Internet öffnen. Besser über Tailscale, WireGuard, SSH-Tunnel oder Reverse Proxy mit Auth.

`/state`, `/health` und `/ready` zeigen immer die **aktuell neu berechnete** Routing-Entscheidung.  
Dateibasierte Wartungs-Overrides werden im Monitoring sofort sichtbar (ohne Neustart und ohne neue Spieler-Verbindung).

```bash
touch /var/lib/mc-failover/force_fallback
curl http://127.0.0.1:8080/state
```

Danach sollte in der Antwort `active_target="FALLBACK"` und `routing_reason="force_fallback_file"` stehen.

Wenn du auf einem VPS arbeitest, lasse `listen_host` am besten auf `127.0.0.1` und greife per SSH-Tunnel oder Tailscale darauf zu.


## PROXY-Protokoll

Dieser Proxy unterstützt **nur PROXY protocol v1** (kein v2). Optional kann er PROXY-Header von einem vertrauenswürdigen Downstream-Proxy annehmen und/oder an Upstream-Server weitergeben.

⚠️ Sicherheitswarnung: `accept=true` niemals für untrusted Internet-Clients aktivieren. Zugriff einschränken und `trusted_proxy_ips` setzen.

Beispiel 1 (nur akzeptieren):
```toml
[proxy_protocol]
accept = true
send = false
version = 1
trusted_proxy_ips = ["100.64.0.1", "127.0.0.1"]
```

Beispiel 2 (akzeptieren + an Velocity weitergeben):
```toml
[proxy_protocol]
accept = true
send = true
version = 1
trusted_proxy_ips = ["100.64.0.1"]
```

Prüfe die Velocity-Dokumentation für die passende PROXY-protocol Einstellung.

HAProxy-Backend (v1):
```haproxy
backend mc_failover
    mode tcp
    server failover 100.64.0.20:25565 send-proxy
```

`send-proxy-v2` ist PROXY protocol v2 und wird hier nicht unterstützt.

---

[English](README.md)
