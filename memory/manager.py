"""Memory manager for Claude Code-style context and learnings."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger('ai-sre-agent.memory')


class MemoryManager:
    """Manages hierarchical CLAUDE.md context and agent learnings."""

    def __init__(self, working_dir: Path = None):
        self.working_dir = working_dir or Path.cwd()
        self.claude_dir = self.working_dir / '.claude'
        self.memory_file = self.claude_dir / 'memory.md'
        self.rules_dir = self.claude_dir / 'rules'

        # Ensure directories exist
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        self.rules_dir.mkdir(parents=True, exist_ok=True)

        # Initialize memory file if it doesn't exist
        if not self.memory_file.exists():
            self._init_memory_file()

    def _init_memory_file(self):
        """Create initial memory.md template."""
        template = """# Agent Memory

## Learned Entity Mappings
<!-- Entity name -> Home Assistant entity_id mappings -->

## Successful Remediation Patterns
<!-- What worked in the past -->

## False Positives
<!-- Issues to ignore or handle differently -->

## User Preferences
<!-- Observed user behavior patterns -->

---
*Created: {timestamp}*
""".format(timestamp=datetime.utcnow().isoformat())

        self.memory_file.write_text(template)
        logger.info(f"Initialized memory file: {self.memory_file}")

    def load_all_context(self) -> str:
        """Load hierarchical CLAUDE.md files like Claude Code.

        Order (highest to lowest priority):
        1. ~/.claude/CLAUDE.md - Global preferences
        2. ~/server/CLAUDE.md - Infrastructure context
        3. ./CLAUDE.md - Project config
        4. ./.claude/memory.md - Agent learnings
        5. ./.claude/rules/*.md - Modular rules
        """
        context_parts = []

        # 1. Global preferences
        global_claude = Path.home() / '.claude' / 'CLAUDE.md'
        if global_claude.exists():
            context_parts.append(f"[~/.claude/CLAUDE.md]\n{self._read_truncated(global_claude)}")

        # 2. Server-wide context (walk up from working dir)
        server_claude = Path.home() / 'server' / 'CLAUDE.md'
        if server_claude.exists():
            context_parts.append(f"[~/server/CLAUDE.md]\n{self._read_truncated(server_claude)}")

        # 3. Project config
        project_claude = self.working_dir / 'CLAUDE.md'
        if project_claude.exists():
            context_parts.append(f"[./CLAUDE.md]\n{self._read_truncated(project_claude)}")

        # 4. Agent learnings
        if self.memory_file.exists():
            memory_content = self.memory_file.read_text()
            if memory_content.strip():
                context_parts.append(f"[.claude/memory.md]\n{memory_content}")

        # 5. Modular rules
        if self.rules_dir.exists():
            for rule_file in sorted(self.rules_dir.glob('*.md')):
                rule_content = rule_file.read_text()
                if rule_content.strip():
                    context_parts.append(f"[.claude/rules/{rule_file.name}]\n{rule_content}")

        return '\n\n'.join(context_parts) if context_parts else "(No context files found)"

    def _read_truncated(self, path: Path, max_lines: int = 100) -> str:
        """Read file, truncating if too long."""
        try:
            content = path.read_text()
            lines = content.split('\n')
            if len(lines) > max_lines:
                return '\n'.join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"
            return content
        except Exception as e:
            logger.warning(f"Failed to read {path}: {e}")
            return f"(Error reading file: {e})"

    def get_memory(self) -> str:
        """Read current memory.md content."""
        if not self.memory_file.exists():
            return "(No memory file)"
        return self.memory_file.read_text()

    def add_memory(self, text: str, section: str = "Learned Entity Mappings") -> bool:
        """Append to memory.md with timestamp.

        Args:
            text: The learning to add
            section: Which section to add it to

        Returns:
            True if successful
        """
        try:
            content = self.memory_file.read_text() if self.memory_file.exists() else ""

            # Find the section and append
            timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
            entry = f"- {text} ({timestamp})\n"

            if f"## {section}" in content:
                # Insert after section header
                parts = content.split(f"## {section}")
                if len(parts) == 2:
                    header, rest = parts
                    # Find end of section (next ## or ---)
                    lines = rest.split('\n')
                    insert_idx = 1  # After section header
                    for i, line in enumerate(lines[1:], 1):
                        if line.startswith('## ') or line.startswith('---'):
                            insert_idx = i
                            break
                        if line.strip() and not line.startswith('<!--'):
                            insert_idx = i + 1

                    lines.insert(insert_idx, entry.strip())
                    content = header + f"## {section}" + '\n'.join(lines)
            else:
                # Append at end before footer
                if '---' in content:
                    parts = content.rsplit('---', 1)
                    content = parts[0] + f"\n## {section}\n{entry}\n---" + parts[1]
                else:
                    content += f"\n## {section}\n{entry}"

            # Update timestamp
            content = self._update_timestamp(content)

            self.memory_file.write_text(content)
            logger.info(f"Added to memory: {text[:50]}...")
            return True

        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            return False

    def _update_timestamp(self, content: str) -> str:
        """Update the last-updated timestamp in content."""
        import re
        timestamp = datetime.utcnow().isoformat()
        # Replace existing timestamp
        content = re.sub(
            r'\*Last updated:.*\*',
            f'*Last updated: {timestamp}*',
            content
        )
        content = re.sub(
            r'\*Created:.*\*',
            f'*Last updated: {timestamp}*',
            content
        )
        return content

    def clear_memory(self) -> bool:
        """Clear memory.md and reinitialize."""
        try:
            self._init_memory_file()
            logger.info("Memory cleared")
            return True
        except Exception as e:
            logger.error(f"Failed to clear memory: {e}")
            return False

    def list_rules(self) -> list:
        """List .claude/rules/*.md files."""
        if not self.rules_dir.exists():
            return []
        return [f.stem for f in sorted(self.rules_dir.glob('*.md'))]

    def get_rule(self, name: str) -> Optional[str]:
        """Read a rule file.

        Args:
            name: Rule name (without .md extension)

        Returns:
            Rule content or None if not found
        """
        rule_file = self.rules_dir / f"{name}.md"
        if rule_file.exists():
            return rule_file.read_text()
        return None

    def add_rule(self, name: str, content: str) -> bool:
        """Create or append to a rule file.

        Args:
            name: Rule name (without .md extension)
            content: Content to add

        Returns:
            True if successful
        """
        try:
            rule_file = self.rules_dir / f"{name}.md"
            timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M')

            if rule_file.exists():
                # Append to existing
                existing = rule_file.read_text()
                rule_file.write_text(f"{existing}\n\n## Added {timestamp}\n{content}")
            else:
                # Create new
                rule_file.write_text(f"# {name.title()} Rules\n\n{content}\n\n---\n*Created: {timestamp}*")

            logger.info(f"Updated rule: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to add rule: {e}")
            return False

    def get_context_files(self) -> list:
        """List all loaded context files with their paths."""
        files = []

        global_claude = Path.home() / '.claude' / 'CLAUDE.md'
        if global_claude.exists():
            files.append(('~/.claude/CLAUDE.md', 'global'))

        server_claude = Path.home() / 'server' / 'CLAUDE.md'
        if server_claude.exists():
            files.append(('~/server/CLAUDE.md', 'server'))

        project_claude = self.working_dir / 'CLAUDE.md'
        if project_claude.exists():
            files.append(('./CLAUDE.md', 'project'))

        if self.memory_file.exists():
            files.append(('.claude/memory.md', 'memory'))

        for rule_file in sorted(self.rules_dir.glob('*.md')):
            files.append((f'.claude/rules/{rule_file.name}', 'rule'))

        return files
