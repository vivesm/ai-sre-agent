# SRE Agent Notes

Persistent notes to help me get things right the first time.

## Core Operating Rules

### Rule 1: Self-Improving Knowledge Base
Every conversation should improve this database. When I learn something new about the infrastructure (servers, entity mappings, quirks, workarounds), I MUST document it immediately. No knowledge should be lost between sessions.

Examples of what to capture:
- New server discoveries (like learning Tesseract is a Synology NAS)
- Non-obvious entity mappings (like remote.tv_master for Samsung TV power)
- SSH access methods and quirks
- Service locations and dependencies
- Troubleshooting solutions that worked

If I "figure something out" during a conversation, it goes in this file or server-inventory.md BEFORE the session ends.

## Entity Mappings (Non-Obvious)

### TVs
- Main Bedroom TV: Use `remote.tv_master` (NOT media_player.main_bedroom_tv)
  - Turn off: remote.turn_off with entity_id remote.tv_master
  - The media_player entity exists but doesn't control power

## Lessons Learned

### 2026-01-09: TV Entity Discovery
When looking for TV controls, check BOTH media_player AND remote domains. Some TVs (like Samsung via SmartThings) expose a remote entity for power control while the media_player is for playback state only.

---
Add new notes below as we learn more.
