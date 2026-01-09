# AI SRE Agent

AI-powered SRE assistant with Signal chatbot for home server monitoring.

## Current State
**Milestone:** GitHub Release (7/7)
**Status:** Code complete, awaiting gh token update for push

## How to Work

### Planning Required
- Use Plan Mode for any new features
- Ask clarifying questions when requirements are ambiguous
- Research open-source patterns before implementing

### Repository Rules
- No direct commits to main
- All secrets in `.env` (never in code)
- Examples use sanitized/fake data only
- Update docs after completing work

## Quick Reference

### Run the Bot
```bash
cp .env.example .env  # Edit with real values
pip install -r requirements.txt
python signal_chat.py
```

### Test System Context
```bash
python -c "from signal_chat import get_system_context; print(get_system_context())"
```

### Check Bot Status
```bash
ps aux | grep signal_chat
tail -f /tmp/signal-chat.log
```

## Architecture
See [docs/architecture.md](docs/architecture.md)

## Hard Constraints
- Requires signal-cli-rest-api running
- Requires Claude Code CLI authenticated
- Python 3.12+
- Secrets NEVER in code or docs

## TODO Tracking (Mandatory)

**Canonical TODO file:** `/docker/webdav/vault/ObsidianVault/todo.md` on Atom

**Rule:** If you notice an issue not fixed in retro, append to canonical TODO with:
- Date
- Short title
- Context
- Next action
- Owner (me | claude)

**No secrets or unsanitized data in TODO.**

## Documentation Links
- [Project Spec](docs/ProjectSpec.md) - Requirements and scope
- [Architecture](docs/architecture.md) - System design
- [Status](docs/status.md) - Current progress
- [Changelog](docs/changelog.md) - Version history
