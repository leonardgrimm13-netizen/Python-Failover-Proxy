# Minecraft Python Failover Proxy

Production-focused TCP failover proxy for Minecraft.

## Features
- Maintenance modes (auto/force_main/force_fallback)
- Recovery wait (`min_recovery_seconds`)
- CLI checks (`--check-config`, `--test-main`, `--test-fallback`, `--test-healthcheck`, `--print-effective-config`)
- Advanced `minecraft_status` filters (version, MOTD, players, latency)
- Monitoring endpoint configuration
- Docker and systemd packaging
- PROXY protocol v1 options with trusted CIDR list

## Limits
- No live migration of already connected players.
- Not a full Velocity replacement.
- PROXY protocol v1 only.
- MOTD filters are not full plugin-readiness checks.
- Monitoring defaults to local bind and disabled.
