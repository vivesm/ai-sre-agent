# Server Inventory

Last updated: 2026-01-09

## ATOM (Primary Home Server)
- **Hostname:** Atom
- **OS:** Ubuntu 24.04 LTS
- **Role:** Home automation hub, Docker host
- **Hardware:** Intel i7-7500U, 16GB RAM
- **Storage:** 233GB SSD (44% used)
- **IP:** 192.168.7.223 (static)
- **Services:** Home Assistant, MQTT, Ring-MQTT, Scrypted, Portainer, Caddy
- **Containers:** 27 running
- **Access:** Direct (this machine)
- **URLs:** https://home.vives.io, https://auth.vives.io

## BLACKEST (Media & Backup Mac)
- **Hostname:** Blackest.local
- **OS:** macOS
- **Hardware:** MacBook Pro M1 Max, 64GB RAM
- **Storage:** 1.8TB SSD (4% used)
- **Role:** Media storage, iPhoto library, backup target
- **Access:** ssh blackest.local
- **Notes:** Despite hostname, actually a MacBook Pro 18,4

## SAGAN (Signal API Server)
- **Hostname:** Sagan.local
- **OS:** macOS
- **Hardware:** Mac mini M4, 16GB RAM
- **Storage:** 460GB SSD (16% used)
- **Role:** Signal API server, backups
- **Access:** ssh sagan.local

## TESSERACT (Synology NAS)
- **Hostname:** tesseract
- **Type:** Synology NAS
- **Memory:** 62GB total
- **Role:** Network storage, media server
- **IP:** 192.168.4.56 (different subnet!)
- **Access:** ssh melvin@192.168.4.56 (port 22, tesseract.local doesn't resolve)
- **Notes:**
  - System partition (2.3GB) runs tight at 81%
  - Main storage on /volume1
  - On 192.168.4.x subnet (not 192.168.7.x like Atom)

## Network Overview
- All servers on 192.168.7.x subnet
- Guest WiFi isolated on 192.168.50.x
- Caddy reverse proxy on Atom handles external access
- Authelia 2FA at auth.vives.io

## Projects & Documentation

For detailed context on any project, read its CLAUDE.md file.

### Web Applications
- **home.vives.io** (Home Assistant): `/home/melvin/server/home.vives.io/CLAUDE.md`
- **love.vives.io** (Relationship Quest): `/docker/webdav/vault/ObsidianVault/Projects/love.vives.io/CLAUDE.md`
- **share.vives.io** (TextShare): `/home/melvin/server/share.vives.io/textshare/CLAUDE.md`
- **wellness.vives.io**: `/docker/webdav/vault/ObsidianVault/Projects/wellness.vives.io/CLAUDE.md`
- **todo.vives.io** (FamilyToDo): `/docker/webdav/vault/ObsidianVault/Projects/todo.vives.io/FamilyToDo/CLAUDE.md`

### Infrastructure
- **ai-sre-agent** (this bot): `/home/melvin/server/ai-sre-agent/CLAUDE.md`
- **atom server**: `/docker/webdav/vault/ObsidianVault/Infrastructure/01-servers/atom/CLAUDE.md`
- **sbvps server**: `/docker/webdav/vault/ObsidianVault/Infrastructure/01-servers/sbvps/CLAUDE.md`
- **atom-admin**: `/docker/webdav/vault/ObsidianVault/Projects/atom-admin/CLAUDE.md`
- **atom-system**: `/docker/webdav/vault/ObsidianVault/Projects/atom-system/CLAUDE.md`
