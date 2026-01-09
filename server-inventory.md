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
- **Access:** ssh tesseract (currently unreachable via that hostname)
- **Notes:**
  - System partition (2.3GB) runs tight at 81%
  - Main storage on /volume1
  - May need alternate hostname/IP for SSH

## Network Overview
- All servers on 192.168.7.x subnet
- Guest WiFi isolated on 192.168.50.x
- Caddy reverse proxy on Atom handles external access
- Authelia 2FA at auth.vives.io
