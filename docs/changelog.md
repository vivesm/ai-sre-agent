# AI SRE Agent - Changelog

All notable changes to this project.

---

## [Unreleased]
### Added
- PSB documentation structure (ProjectSpec, architecture, status, changelog)
- CLAUDE.md project memory file

---

## [0.2.0] - 2026-01-09
### Added
- Quick responses for simple queries (no Claude API call)
- "..." thinking indicator for complex queries
- Conversation history (last 20 messages persisted)
- CLAUDE.md context loading from multiple locations
- SSH access to remote servers (blackest.local, sagan.local)
- Home Assistant API integration with device control
- 120 second timeout for long operations

### Changed
- Moved all secrets to .env file
- Used python-dotenv for configuration
- Increased Claude timeout from 60s to 120s

### Security
- Sanitized all examples and documentation
- Added comprehensive .gitignore

---

## [0.1.0] - 2026-01-08
### Added
- Initial Signal chatbot using signalbot library
- Real-time system metrics (CPU, memory, disk, containers, IP, uptime)
- Claude Code CLI integration with --dangerously-skip-permissions
- Basic message handling and response

### Changed
- Migrated from custom websocket implementation to signalbot library

### Fixed
- URL format for signal-cli-rest-api (removed http:// prefix)
- Disk info slicing error with None fallback
