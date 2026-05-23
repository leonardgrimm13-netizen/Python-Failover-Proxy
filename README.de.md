# Minecraft Python Failover Proxy

Praxisnaher TCP-Failover-Proxy für Minecraft-Server.

## Funktionen
- Wartungsmodus (auto/force_main/force_fallback)
- Recovery-Wartezeit (`min_recovery_seconds`)
- CLI-Prüfbefehle
- Erweiterte `minecraft_status`-Filter (Version, MOTD, Spieler, Latenz)
- Monitoring-Konfiguration
- Docker- und systemd-Paketierung
- PROXY protocol v1 mit vertrauenswürdigen IP/CIDR-Netzen

## Grenzen
- Keine Live-Migration bereits verbundener Spieler.
- Kein vollwertiger Ersatz für Velocity.
- Nur PROXY protocol v1.
- MOTD-Filter ersetzen keine echte Plugin-Readiness-Prüfung.
- Monitoring ist standardmäßig lokal/deaktiviert.
