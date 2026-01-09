# AI SRE Agent - Project Specification

## Product Requirements

### Project Goal
**Type:** Prototype → Production

An AI-powered SRE assistant that monitors home server infrastructure and responds to queries via Signal messenger, enabling mobile-first server management.

### Target Users
- Home server administrators
- Self-hosters managing Docker-based infrastructure
- Users wanting natural language interaction with their servers

### Problems Solved
1. **Remote monitoring** - Check server status from anywhere via Signal
2. **Natural language queries** - Ask "what's the CPU temp?" instead of SSH commands
3. **Proactive alerts** - Get notified of issues before they become critical
4. **Home automation control** - Control Home Assistant devices via chat
5. **Multi-server management** - Query multiple servers from one interface

### User Workflows

**Quick Status Check:**
1. User sends "status" via Signal
2. Bot responds with CPU, memory, disk, container health (instant, no AI call)

**Complex Query:**
1. User sends "what containers are unhealthy on sagan?"
2. Bot sends "..." thinking indicator
3. Bot SSHs to sagan, runs docker ps, analyzes with Claude
4. Bot responds with findings

**Device Control:**
1. User sends "turn on living room lights"
2. Bot calls Home Assistant API
3. Bot confirms action completed

**Approval Workflow:**
1. Bot detects issue (container unhealthy)
2. Bot creates remediation plan
3. User receives plan via Signal
4. User replies "approve" or "reject"
5. Bot executes or logs accordingly

### MVP Scope

**Included:**
- [x] Signal message receive/send via signalbot
- [x] Real-time system metrics (CPU, memory, disk, containers)
- [x] Quick responses for simple queries
- [x] Claude integration for complex queries
- [x] Conversation history
- [x] Home Assistant API integration
- [x] Multi-server SSH access
- [x] CLAUDE.md context loading

**Explicitly Excluded:**
- [ ] Web dashboard
- [ ] Scheduled reports
- [ ] Multi-user support
- [ ] Voice messages
- [ ] Image/screenshot sharing
- [ ] Kubernetes support

### Milestones

| Milestone | Status | Definition of Done |
|-----------|--------|-------------------|
| 1. Basic chat | ✅ Done | Send message → get response |
| 2. System awareness | ✅ Done | Real metrics in responses |
| 3. Quick responses | ✅ Done | Simple queries answered instantly |
| 4. HA integration | ✅ Done | Can control devices |
| 5. Multi-server | ✅ Done | SSH to other hosts |
| 6. Context loading | ✅ Done | Reads CLAUDE.md files |
| 7. GitHub release | 🔄 In Progress | Public repo with docs |

### Non-Goals & Constraints
- Not a replacement for proper monitoring (Prometheus, Grafana)
- Not designed for high-availability or multi-tenant use
- Requires signal-cli-rest-api running separately
- Requires Claude Code CLI installed and authenticated

---

## Engineering Requirements

### Tech Stack
| Component | Choice | Reason |
|-----------|--------|--------|
| Language | Python 3.12 | Existing ecosystem, signalbot library |
| Signal | signalbot | Mature library, handles websocket complexity |
| AI | Claude Code CLI | Already authenticated, YOLO mode available |
| Config | python-dotenv | Standard .env pattern |

### Alternatives Considered
- **Custom websocket** - Rejected: too complex, signalbot handles it
- **Anthropic API direct** - Rejected: Claude CLI already authenticated
- **Telegram** - Rejected: User preference for Signal privacy

### High-Level Architecture
```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Signal    │────▶│  signal_chat │────▶│ Claude CLI  │
│   (User)    │◀────│    .py       │◀────│   (AI)      │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌─────────┐  ┌──────────┐  ┌─────────┐
        │ System  │  │   Home   │  │  SSH    │
        │ Metrics │  │ Assistant│  │ Servers │
        └─────────┘  └──────────┘  └─────────┘
```

### Data Model
- **Chat History:** JSON file, last 20 messages
- **System Context:** Gathered fresh each query
- **No persistent database required**

### APIs/Contracts
- **Signal:** signalbot library handles protocol
- **Home Assistant:** REST API with Bearer token
- **SSH:** Standard ssh command via subprocess

### Security & Privacy
- Secrets in `.env` (gitignored)
- HA token never logged
- Phone numbers sanitized in examples
- No data sent to external services except Claude API

### Infrastructure Assumptions
- signal-cli-rest-api running on sagan.local:8080
- SSH keys configured for passwordless access
- Claude Code CLI installed and authenticated
- Home Assistant accessible via HTTPS

### Observability
- Python logging to stdout
- Logs include: received messages, response sent, errors
- No metrics collection (future enhancement)

### Local Development

**OS:** Linux (Ubuntu 24.04)
**Runtime:** Python 3.12+
**Package Manager:** pip

**Setup:**
```bash
cd ai-sre-agent
cp .env.example .env
# Edit .env with your values
pip install -r requirements.txt
python signal_chat.py
```

**Required Services:**
- signal-cli-rest-api (can be remote)
- Home Assistant (optional, for device control)
