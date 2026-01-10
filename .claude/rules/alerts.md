# Alert Rules

## Severity Mapping
- **Critical**: Service down, security issue, data loss risk
- **Warning**: Degraded performance, high resource usage
- **Info**: Routine maintenance, informational

## Suppression Rules
- Ignore load spikes during Immich video transcoding
- Ignore brief container restarts during updates (watchtower)
- Suppress duplicate alerts for 2 hours

## Escalation
- Critical: Signal + Mobile push + TTS announcement
- Warning: Signal + Mobile push
- Info: Signal only (or email)

---
*Created: 2026-01-09*
