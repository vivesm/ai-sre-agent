# AI SRE Agent - Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Signal Messenger                          │
└─────────────────────────────┬───────────────────────────────────┘
                              │ WebSocket
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    signal-cli-rest-api                           │
│                    (sagan.local:8080)                            │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      signal_chat.py                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Quick       │  │ Claude      │  │ Context Providers       │  │
│  │ Response    │  │ Integration │  │ - System metrics        │  │
│  │ (no AI)     │  │ (complex)   │  │ - Chat history          │  │
│  └─────────────┘  └─────────────┘  │ - CLAUDE.md files       │  │
│                                     │ - HA API info           │  │
│                                     │ - SSH server info       │  │
│                                     └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Local System   │  │ Home Assistant  │  │  Remote Servers │
│  - /proc        │  │ - REST API      │  │  - SSH access   │
│  - /sys         │  │ - Device control│  │  - blackest     │
│  - docker       │  └─────────────────┘  │  - sagan        │
└─────────────────┘                       └─────────────────┘
```

## Component Details

### signal_chat.py (Main Entry Point)

**Responsibilities:**
- Receive messages via signalbot library
- Route to quick response or Claude
- Send responses back to Signal

**Key Functions:**
| Function | Purpose |
|----------|---------|
| `get_system_context()` | Gather CPU, memory, disk, containers, IP, uptime |
| `load_claude_context()` | Read CLAUDE.md files for infrastructure knowledge |
| `is_simple_query()` | Detect queries that don't need AI |
| `get_quick_response()` | Answer simple queries from cached data |
| `load_chat_history()` | Load last 10 messages for context |
| `save_chat_history()` | Persist conversation for continuity |

### Context Providers

**System Metrics:**
- CPU temp: `/sys/class/thermal/thermal_zone0/temp`
- Memory: `/proc/meminfo`
- Disk: `df -h`
- Containers: `docker ps`
- Load: `/proc/loadavg`
- Public IP: `curl ifconfig.me`
- Uptime: `uptime -p`

**Infrastructure Knowledge:**
- Reads CLAUDE.md from: `~/CLAUDE.md`, `~/server/CLAUDE.md`, `~/.claude/CLAUDE.md`
- Extracts key sections (headers, emergency procedures, commands)
- Limits to 800 chars per file to manage context

### Quick Response Patterns

Queries matching these patterns skip Claude entirely:
- `status`, `temp`, `cpu`, `memory`, `disk`, `ip`, `uptime`, `containers`

Benefits: <1 second response vs 5-30 seconds with Claude

### Claude Integration

**Invocation:**
```bash
claude -p "<prompt>" --output-format text --dangerously-skip-permissions
```

**Prompt Structure:**
1. Infrastructure knowledge (from CLAUDE.md)
2. Current system state (real metrics)
3. Recent conversation (last 3 exchanges)
4. User message
5. Instructions (be concise, use data, no permission asking)

**Timeout:** 120 seconds (for SSH operations)

## Data Flow

### Simple Query Flow
```
User: "cpu temp"
  → is_simple_query() = True
  → get_system_context()
  → get_quick_response() extracts temp
  → Response: "27.8°C"
  → save_chat_history()
```

### Complex Query Flow
```
User: "what containers are unhealthy on sagan?"
  → is_simple_query() = False
  → send "..." thinking indicator
  → get_system_context()
  → load_claude_context()
  → load_chat_history()
  → Build prompt with all context
  → claude -p "<prompt>" --dangerously-skip-permissions
  → Claude SSHs to sagan, runs docker ps
  → Response sent to user
  → save_chat_history()
```

## External Dependencies

| Dependency | Purpose | Location |
|------------|---------|----------|
| signal-cli-rest-api | Signal protocol | sagan.local:8080 |
| Claude Code CLI | AI responses | Local installation |
| Home Assistant | Device control | home.vives.io |
| SSH | Remote servers | blackest.local, sagan.local |

## Open Source Patterns Referenced

- **signalbot library:** [pypi.org/project/signalbot](https://pypi.org/project/signalbot/)
- **Claude Code CLI patterns:** [Anthropic best practices](https://www.anthropic.com/engineering/claude-code-best-practices)
- **ChatOps patterns:** [claude-code-telegram](https://github.com/RichardAtCT/claude-code-telegram)
