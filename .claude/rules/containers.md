# Container Rules

## Critical Containers (Never Auto-Restart)
- homeassistant - Core automation, requires careful restart
- mosquitto - MQTT broker, Ring depends on it
- authelia - Auth gateway, affects all services

## Safe to Restart
- ring-mqtt - Can restart without side effects
- homer - Dashboard only
- watchtower - Update checker

## Restart Order
If multiple containers need restart:
1. mosquitto first (MQTT broker)
2. ring-mqtt second (depends on mosquitto)
3. homeassistant last (depends on MQTT)

---
*Created: 2026-01-09*
