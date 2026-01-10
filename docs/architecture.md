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
│  ┌─────────────┐  ┌─────────────────────────────────────────┐   │
│  │ Mode Switch │  │ Claude Agent SDK (claude_sdk.py)        │   │
│  │ /sre        │  │ - Full Claude Code capabilities         │   │
│  │ /operator   │  │ - Read, Write, Bash, Glob, Grep tools   │   │
│  └─────────────┘  │ - SSH, HA REST API, autonomous ops      │   │
│                   └─────────────────────────────────────────┘   │
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
- Route mode switches locally, everything else to Claude SDK
- Send responses back to Signal

**Key Functions:**
| Function | Purpose |
|----------|---------|
| `get_system_context()` | Gather CPU, memory, disk, containers, IP, uptime |
| `load_claude_context()` | Read CLAUDE.md files for infrastructure knowledge |
| `load_memory_files()` | Load server-inventory.md and sre-notes.md |
| `load_chat_history()` | Load last 10 messages for context |
| `save_chat_history()` | Persist conversation for continuity |

### claude_sdk.py (Claude Agent SDK Wrapper)

**Capabilities:**
- Same tools as Claude Code: Read, Write, Bash, Glob, Grep
- Autonomous file exploration and command execution
- SSH to remote servers without manual configuration
- No permission prompts (bypass mode)

**Key Functions:**
| Function | Purpose |
|----------|---------|
| `query_claude()` | Async function to send query with full tool access |
| `query_sync()` | Sync wrapper for non-async contexts (agent.py) |

### Context Providers

**System Metrics:**
- CPU temp: `/sys/class/thermal/thermal_zone0/temp`
- Memory: `/proc/meminfo`
- Disk: `df -h`
- Containers: `docker ps`
- Load: `/proc/loadavg`
- Public IP: `curl ifconfig.me`
- Uptime: `uptime -p`

**Memory Files:**
- `server-inventory.md` - Server specs and network info
- `sre-notes.md` - Learnings and quirks

**Infrastructure Knowledge:**
- Reads CLAUDE.md from: `~/CLAUDE.md`, `~/server/CLAUDE.md`, `~/.claude/CLAUDE.md`

## Data Flow

### Mode Switch Flow
```
User: "/operator"
  → Local handler (no Claude)
  → Response: "Entering Operator mode..."
```

### Query Flow (SDK)
```
User: "what containers are unhealthy on sagan?"
  → get_system_context()
  → load_claude_context()
  → load_memory_files()
  → load_chat_history()
  → await query_claude(message, system_prompt)
  → SDK uses Bash tool to SSH to sagan
  → Response sent to user
  → save_chat_history()
```

## External Dependencies

| Dependency | Purpose | Location |
|------------|---------|----------|
| signal-cli-rest-api | Signal protocol | sagan.local:8080 |
| claude-agent-sdk | Full Claude Code tools | pip package |
| Claude Code CLI | Backend for SDK | ~/.nvm/.../bin/claude |
| Home Assistant | Device control | home.vives.io |
| SSH | Remote servers | blackest.local, sagan.local |

## Modes

| Mode | Trigger | Purpose |
|------|---------|---------|
| SRE | `/sre` | Server monitoring, alerts, plans, HA control |
| Operator | `/operator` | Config changes, memory edits |

## Open Source Patterns Referenced

- **signalbot library:** [pypi.org/project/signalbot](https://pypi.org/project/signalbot/)
- **Claude Agent SDK:** [claude-agent-sdk](https://pypi.org/project/claude-agent-sdk/)
- **ChatOps patterns:** [claude-code-telegram](https://github.com/RichardAtCT/claude-code-telegram)
