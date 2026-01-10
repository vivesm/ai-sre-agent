#!/usr/bin/env python3
"""
AI SRE Agent - Plan-First Server Monitoring

Monitors server health, analyzes issues with Claude Code CLI,
proposes fix plans, and executes only after human approval.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from collectors.docker import DockerCollector
from collectors.system import SystemCollector
from collectors.logs import LogCollector
from analyzer.claude import ClaudeAnalyzer
from actions.notify import Notifier
from actions.execute import Executor
from actions.signal_receiver import SignalReceiver
from modes import Mode
from dedup import AlertDeduplicator
from memory import MemoryManager

# Setup logging
def setup_logging():
    """Setup logging with fallback for permission issues."""
    handlers = [logging.StreamHandler()]

    # Try to log to /var/log first, fall back to local directory
    log_paths = [
        '/var/log/ai-sre-agent.log',
        Path(__file__).parent / 'data' / 'agent.log'
    ]

    for log_path in log_paths:
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(log_path))
            break
        except PermissionError:
            continue

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )

setup_logging()
logger = logging.getLogger('ai-sre-agent')


class SREAgent:
    """Main SRE Agent orchestrator."""

    def __init__(self, config_path: str = 'config/config.yaml'):
        self.config = self._load_config(config_path)
        self.dry_run = self.config.get('agent', {}).get('dry_run', True)

        # Initialize components
        self.collectors = {
            'docker': DockerCollector(self.config.get('collectors', {}).get('docker', {})),
            'system': SystemCollector(self.config.get('collectors', {}).get('system', {})),
            'logs': LogCollector(self.config.get('collectors', {}).get('logs', {})),
        }
        self.analyzer = ClaudeAnalyzer(self.config.get('llm', {}))
        self.notifier = Notifier(self.config.get('notifications', {}))
        self.executor = Executor(self.config.get('safety', {}))
        self.signal_receiver = SignalReceiver(self.config.get('notifications', {}))

        # Alert deduplication
        dedup_config = self.config.get('agent', {}).get('dedup', {})
        self.deduplicator = AlertDeduplicator(
            state_file=Path('data/alert_state.json'),
            suppress_hours=dedup_config.get('suppress_hours', 2.0)
        )

        # Memory manager for operator mode
        self.memory = MemoryManager(working_dir=Path.cwd())

        # State
        self.plans_dir = Path('data/plans')
        self.history_dir = Path('data/history')
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def _load_config(self, config_path: str) -> dict:
        """Load configuration from YAML file."""
        config_file = Path(config_path)
        if not config_file.exists():
            logger.warning(f"Config file {config_path} not found, using defaults")
            return {}

        with open(config_file) as f:
            return yaml.safe_load(f) or {}

    def collect_evidence(self) -> dict:
        """Gather evidence from all collectors."""
        evidence = {
            'timestamp': datetime.utcnow().isoformat(),
            'hostname': os.uname().nodename,
            'issues': [],
            'metrics': {}
        }

        for name, collector in self.collectors.items():
            try:
                result = collector.collect()
                evidence['metrics'][name] = result.get('metrics', {})
                evidence['issues'].extend(result.get('issues', []))
            except Exception as e:
                logger.error(f"Collector {name} failed: {e}")
                evidence['issues'].append({
                    'source': name,
                    'type': 'collector_error',
                    'message': str(e)
                })

        return evidence

    def _has_pending_plan_for_issue(self, issue_type: str) -> bool:
        """Check if a pending plan already exists for this issue type."""
        for plan_file in self.plans_dir.glob('*.json'):
            try:
                with open(plan_file) as f:
                    plan = json.load(f)
                for ev in plan.get('evidence', []):
                    if issue_type in str(ev).lower():
                        return True
            except Exception:
                continue
        return False

    def analyze_and_plan(self, evidence: dict) -> Optional[dict]:
        """Send evidence to Claude for analysis and plan generation."""
        if not evidence.get('issues'):
            logger.info("No issues detected, skipping analysis")
            return None

        # Check for existing pending plan for same issue type
        primary_issue = evidence['issues'][0].get('type', '') if evidence.get('issues') else ''
        if primary_issue and self._has_pending_plan_for_issue(primary_issue):
            logger.info(f"Skipping analysis - pending plan exists for {primary_issue}")
            return None

        logger.info(f"Analyzing {len(evidence['issues'])} issues...")

        try:
            plan = self.analyzer.analyze(evidence)

            if plan and plan.get('plan'):
                # Save plan to file
                plan_id = datetime.now().strftime('%m%d_%H%M%S')
                plan['plan_id'] = plan_id
                plan['created_at'] = datetime.utcnow().isoformat()
                plan['status'] = 'pending'

                plan_file = self.plans_dir / f"{plan_id}.json"
                with open(plan_file, 'w') as f:
                    json.dump(plan, f, indent=2)

                logger.info(f"Plan {plan_id} created: {plan.get('summary')}")
                return plan
            else:
                logger.info("No actionable plan generated")
                return None

        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return None

    def notify_user(self, plan: dict) -> bool:
        """Send notification about pending plan."""
        try:
            result = self.notifier.send_plan_notification(plan)

            # Re-save plan if notification_timestamp was added (for reaction matching)
            if plan.get('notification_timestamp'):
                plan_file = self.plans_dir / f"{plan['plan_id']}.json"
                with open(plan_file, 'w') as f:
                    json.dump(plan, f, indent=2)
                logger.debug(f"Saved notification_timestamp {plan['notification_timestamp']} for plan {plan['plan_id']}")

            return result
        except Exception as e:
            logger.error(f"Notification failed: {e}")
            return False

    def check_approvals(self) -> list:
        """Check for approved plans ready for execution."""
        approved = []

        for plan_file in self.plans_dir.glob('*.json'):
            try:
                with open(plan_file) as f:
                    plan = json.load(f)

                if plan.get('status') == 'approved':
                    approved.append(plan)
            except Exception as e:
                logger.error(f"Error reading plan {plan_file}: {e}")

        return approved

    def execute_plan(self, plan: dict) -> dict:
        """Execute an approved plan."""
        plan_id = plan.get('plan_id')
        logger.info(f"Executing plan {plan_id}...")

        if self.dry_run:
            logger.info(f"[DRY-RUN] Would execute: {plan.get('summary')}")
            result = {'success': True, 'dry_run': True, 'steps': []}
        else:
            result = self.executor.execute(plan)

        # Update plan status
        plan['status'] = 'completed' if result.get('success') else 'failed'
        plan['executed_at'] = datetime.utcnow().isoformat()
        plan['result'] = result

        # Move to history
        plan_file = self.plans_dir / f"{plan_id}.json"
        history_file = self.history_dir / f"{plan_id}.json"

        with open(history_file, 'w') as f:
            json.dump(plan, f, indent=2)

        if plan_file.exists():
            plan_file.unlink()

        # Notify result
        self.notifier.send_result_notification(plan)

        return result

    def run_once(self):
        """Run a single monitoring cycle."""
        logger.info("Starting monitoring cycle...")

        # 1. Collect evidence
        evidence = self.collect_evidence()
        all_issues = evidence.get('issues', [])

        # 2. Check for existing approved plans first
        approved_plans = self.check_approvals()
        for plan in approved_plans:
            self.execute_plan(plan)

        # 3. Filter issues through deduplicator (suppress repeats)
        new_issues = [
            issue for issue in all_issues
            if self.deduplicator.should_alert(issue)
        ]

        # 4. Clear resolved alerts (issues that are no longer occurring)
        self.deduplicator.clear_resolved(all_issues)

        # 5. Analyze only NEW issues and create plans
        if new_issues:
            logger.info(f"Processing {len(new_issues)} new issues (suppressed {len(all_issues) - len(new_issues)})")
            filtered_evidence = {**evidence, 'issues': new_issues}
            plan = self.analyze_and_plan(filtered_evidence)
            if plan:
                self.notify_user(plan)
        elif all_issues:
            logger.info(f"All {len(all_issues)} issues suppressed (already alerted)")
        else:
            logger.info("No issues detected")

        logger.info("Monitoring cycle complete")

    def process_signal_commands(self):
        """Poll and process Signal commands."""
        if not self.signal_receiver.enabled:
            return

        commands = self.signal_receiver.poll_messages()

        for cmd in commands:
            action = cmd.get('action')
            plan_id = cmd.get('plan_id')

            logger.info(f"Processing Signal command: {action} {plan_id or ''}")

            # Mode switching (works from any mode)
            if action == 'mode_switch':
                self._handle_mode_switch(cmd)
            # SRE mode commands
            elif action == 'approve':
                self._handle_signal_approve(plan_id)
            elif action == 'reject':
                self._handle_signal_reject(plan_id)
            elif action == 'reaction':
                self._handle_signal_reaction(
                    cmd.get('emoji', ''),
                    cmd.get('target_timestamp')
                )
            elif action == 'status':
                plans = self.list_plans('pending')
                self.signal_receiver.send_status(plans, mode=Mode.SRE)
            elif action == 'help':
                self.signal_receiver.send_help(mode=Mode.SRE)
            elif action == 'chat':
                self._handle_signal_chat(cmd.get('text', ''), cmd.get('raw_text', ''))
            # Operator mode commands
            elif action == 'operator_memory_show':
                self._handle_operator_memory_show()
            elif action == 'operator_memory_add':
                self._handle_operator_memory_add(cmd.get('text', ''))
            elif action == 'operator_memory_clear':
                self._handle_operator_memory_clear()
            elif action == 'operator_rules_list':
                self._handle_operator_rules_list()
            elif action == 'operator_rules_show':
                self._handle_operator_rules_show(cmd.get('name', ''))
            elif action == 'operator_rules_add':
                self._handle_operator_rules_add(cmd.get('name', ''), cmd.get('content', ''))
            elif action == 'operator_context':
                self._handle_operator_context()
            elif action == 'operator_reload':
                self._handle_operator_reload()
            elif action == 'operator_help':
                self._handle_operator_help(cmd.get('topic'))
            elif action == 'operator_chat':
                self._handle_operator_chat(cmd.get('text', ''))

    def _validate_issue_persists(self, plan: dict) -> tuple:
        """Check if the issue that triggered this plan still exists.

        Returns:
            tuple: (persists: bool, message: str)
        """
        try:
            # Re-collect current evidence
            current_evidence = self.collect_evidence()
            current_issues = current_evidence.get('issues', [])

            # If no current issues, the problem is resolved
            if not current_issues:
                return False, "No issues currently detected"

            # Get the original evidence from plan
            plan_evidence = plan.get('evidence', [])

            # If plan has no evidence to compare, assume issue persists
            if not plan_evidence:
                return True, "Cannot validate (no original evidence)"

            # Check if similar issue still exists
            for original in plan_evidence:
                original_lower = str(original).lower()

                for current in current_issues:
                    current_msg = current.get('message', '').lower()
                    current_type = current.get('type', '').lower()

                    # Match by issue type or keywords from original evidence
                    if current_type in original_lower:
                        return True, f"Issue persists: {current_type}"

                    # Check for keyword overlap
                    original_words = set(original_lower.split()[:5])
                    if any(word in current_msg for word in original_words if len(word) > 3):
                        return True, f"Similar issue found: {current.get('message', '')[:50]}"

            return False, "Original issue appears resolved"

        except Exception as e:
            logger.warning(f"Issue validation failed: {e}, assuming issue persists")
            return True, f"Validation error: {e}"

    def _handle_signal_approve(self, plan_id: str = None):
        """Handle approve command from Signal."""
        plans = self.list_plans('pending')

        if not plans:
            self.signal_receiver.send_response("📭 No pending plans to approve.", mode=Mode.SRE)
            return

        # Find the plan to approve
        target_plan = None
        if plan_id:
            # Find by exact ID or partial match
            for plan in plans:
                pid = plan.get('plan_id', '')
                if pid == plan_id or pid.startswith(plan_id) or plan_id in pid:
                    target_plan = plan
                    break

            if not target_plan:
                self.signal_receiver.send_response(
                    f"❓ Plan '{plan_id}' not found.\n\n"
                    f"Available plans: {', '.join(p.get('plan_id', '')[:8] for p in plans[:5])}",
                    mode=Mode.SRE
                )
                return
        else:
            # Use most recent pending plan
            target_plan = plans[0]

        pid = target_plan.get('plan_id')

        # Check if plan is stale and validate issue still persists
        created_at = target_plan.get('created_at', '')
        if created_at:
            try:
                plan_age = datetime.utcnow() - datetime.fromisoformat(created_at)

                # If plan is older than 5 minutes, validate issue still exists
                if plan_age.total_seconds() > 300:
                    self.signal_receiver.send_response(
                        f"🔍 Checking if issue still persists...",
                        mode=Mode.SRE
                    )
                    persists, msg = self._validate_issue_persists(target_plan)

                    if not persists:
                        # Cancel the stale plan
                        self.reject_plan(pid, reason="Issue resolved automatically")
                        self.signal_receiver.send_response(
                            f"✨ Good news! The issue has resolved itself.\n\n"
                            f"Plan {pid} canceled - no action needed.\n"
                            f"Reason: {msg}",
                            mode=Mode.SRE
                        )
                        return
            except Exception as e:
                logger.warning(f"Plan age check failed: {e}")

        # Approve the plan
        if self.approve_plan(pid):
            self.signal_receiver.send_response(
                f"✅ Plan {pid} approved.\n\n"
                f"Executing: {target_plan.get('summary', 'Unknown')}",
                mode=Mode.SRE
            )
            # Execute immediately
            result = self.execute_plan(target_plan)
            if result.get('success'):
                self.signal_receiver.send_response(f"🎉 Plan {pid} executed successfully!", mode=Mode.SRE)
            else:
                self.signal_receiver.send_response(
                    f"❌ Plan {pid} execution failed.\n\n"
                    f"Error: {result.get('error', 'Unknown')}",
                    mode=Mode.SRE
                )
        else:
            self.signal_receiver.send_response(f"❌ Failed to approve plan {pid}", mode=Mode.SRE)

    def _handle_signal_reject(self, plan_id: str = None):
        """Handle reject command from Signal."""
        plans = self.list_plans('pending')

        if not plans:
            self.signal_receiver.send_response("📭 No pending plans to reject.", mode=Mode.SRE)
            return

        # Find the plan to reject
        target_plan = None
        if plan_id:
            for plan in plans:
                pid = plan.get('plan_id', '')
                if pid == plan_id or pid.startswith(plan_id) or plan_id in pid:
                    target_plan = plan
                    break

            if not target_plan:
                self.signal_receiver.send_response(
                    f"❓ Plan '{plan_id}' not found.\n\n"
                    f"Available plans: {', '.join(p.get('plan_id', '')[:8] for p in plans[:5])}",
                    mode=Mode.SRE
                )
                return
        else:
            # Use most recent pending plan
            target_plan = plans[0]

        # Reject the plan
        pid = target_plan.get('plan_id')
        if self.reject_plan(pid, reason="Rejected via Signal"):
            self.signal_receiver.send_response(
                f"🚫 Plan {pid} rejected.\n\n"
                f"Dismissed: {target_plan.get('summary', 'Unknown')}",
                mode=Mode.SRE
            )
        else:
            self.signal_receiver.send_response(f"❌ Failed to reject plan {pid}", mode=Mode.SRE)

    def _handle_signal_reaction(self, emoji: str, target_timestamp: int):
        """Handle emoji reaction to a plan notification message.

        Args:
            emoji: The reaction emoji (e.g., 👍, 👎, 🔍)
            target_timestamp: Timestamp of the message being reacted to
        """
        if not target_timestamp:
            logger.debug("Reaction without target timestamp, ignoring")
            return

        # Define emoji mappings
        approve_emojis = ['👍', '👌', '✅', '🚀', '💪', '🎉']
        reject_emojis = ['👎', '❌', '🚫', '⛔', '🛑']
        investigate_emojis = ['🔍', '🔎']

        # Find plan by notification timestamp
        for plan_file in self.plans_dir.glob('*.json'):
            try:
                with open(plan_file) as f:
                    plan = json.load(f)

                # Compare as int (timestamp might be stored as string in JSON)
                plan_ts = plan.get('notification_timestamp')
                if plan_ts and int(plan_ts) == target_timestamp:
                    plan_id = plan.get('plan_id')
                    logger.info(f"Reaction {emoji} matched plan {plan_id}")

                    if emoji in approve_emojis:
                        self._handle_signal_approve(plan_id)
                        return
                    elif emoji in reject_emojis:
                        self._handle_signal_reject(plan_id)
                        return
                    elif emoji in investigate_emojis:
                        self._handle_signal_investigate(plan)
                        return
                    else:
                        # Unrecognized emoji on a plan message
                        logger.debug(f"Unrecognized reaction {emoji} on plan {plan_id}")
                        return

            except Exception as e:
                logger.error(f"Error reading plan {plan_file}: {e}")

        # No matching plan found - might be reaction to other message
        logger.debug(f"Reaction {emoji} on timestamp {target_timestamp} doesn't match any pending plan")

    def _handle_signal_investigate(self, plan: dict):
        """Reinvestigate an issue and provide fresh analysis.

        Args:
            plan: The original plan dict to reinvestigate
        """
        plan_id = plan.get('plan_id')
        original_summary = plan.get('summary', 'Unknown issue')
        logger.info(f"Reinvestigating plan {plan_id}: {original_summary}")

        # Acknowledge the request
        self.signal_receiver.send_response(
            f"🔍 Reinvestigating: {original_summary[:50]}...",
            mode=Mode.SRE
        )

        # Collect fresh evidence
        evidence = self.collect_evidence()

        # Check if there are current issues
        current_issues = evidence.get('issues', [])

        if not current_issues:
            # Issue resolved
            self.reject_plan(plan_id, reason="Issue resolved - reinvestigation found no problems")
            self.signal_receiver.send_response(
                f"🔎 Reinvestigation complete:\n\n"
                f"✨ Good news! The issue appears resolved.\n"
                f"Plan {plan_id} dismissed.",
                mode=Mode.SRE
            )
            return

        # Re-analyze with Claude, mentioning the original issue
        new_plan = self.analyze_and_plan(evidence)

        if new_plan:
            # Cancel old plan and replace with new one
            self.reject_plan(plan_id, reason=f"Superseded by reinvestigation: {new_plan.get('plan_id')}")

            # Notify about the new plan (already saved by analyze_and_plan)
            self.notify_user(new_plan)

            logger.info(f"Reinvestigation produced new plan: {new_plan.get('plan_id')}")
        else:
            # Analysis didn't produce a plan (might be info-only issues)
            self.signal_receiver.send_response(
                f"🔎 Reinvestigation complete:\n\n"
                f"Current issues detected but no action required.\n"
                f"Original plan {plan_id} remains pending.",
                mode=Mode.SRE
            )

    # ========== Operator Mode Handlers ==========

    def _handle_mode_switch(self, cmd: dict):
        """Handle mode switching command."""
        sender = cmd.get('sender')
        new_mode = cmd.get('mode')

        self.signal_receiver.set_mode(sender, new_mode)
        logger.info(f"Mode switched to {new_mode.value} for {sender}")

        if new_mode == Mode.SRE:
            self.signal_receiver.send_response(
                "👋 Monitoring mode.\n\n"
                "Commands: approve, reject, status, ?\n"
                "Or ask me anything about your system.",
                mode=Mode.SRE
            )
        elif new_mode == Mode.OPERATOR:
            self.signal_receiver.send_response(
                "🔧 Configuration mode.\n\n"
                "Commands:\n"
                "• memory show/add/clear\n"
                "• rules list/show/add\n"
                "• context, reload\n"
                "• /sre to exit",
                mode=Mode.OPERATOR
            )

    def _handle_operator_memory_show(self):
        """Show memory.md contents."""
        content = self.memory.get_memory()
        # Truncate if too long for Signal
        if len(content) > 1500:
            content = content[:1500] + "\n\n... (truncated)"
        self.signal_receiver.send_response(f"📝 Memory:\n\n{content}", mode=Mode.OPERATOR)

    def _handle_operator_memory_add(self, text: str):
        """Add to memory."""
        if not text:
            self.signal_receiver.send_response("❌ Usage: memory add <text>", mode=Mode.OPERATOR)
            return

        if self.memory.add_memory(text):
            self.signal_receiver.send_response(f"✅ Added to memory:\n{text}", mode=Mode.OPERATOR)
        else:
            self.signal_receiver.send_response("❌ Failed to add to memory", mode=Mode.OPERATOR)

    def _handle_operator_memory_clear(self):
        """Clear memory."""
        if self.memory.clear_memory():
            self.signal_receiver.send_response("🗑️ Memory cleared", mode=Mode.OPERATOR)
        else:
            self.signal_receiver.send_response("❌ Failed to clear memory", mode=Mode.OPERATOR)

    def _handle_operator_rules_list(self):
        """List rule files."""
        rules = self.memory.list_rules()
        if rules:
            self.signal_receiver.send_response(
                f"📋 Rules ({len(rules)}):\n" +
                "\n".join(f"• {r}" for r in rules),
                mode=Mode.OPERATOR
            )
        else:
            self.signal_receiver.send_response("📋 No rule files found", mode=Mode.OPERATOR)

    def _handle_operator_rules_show(self, name: str):
        """Show a rule file."""
        if not name:
            self.signal_receiver.send_response("❌ Usage: rules show <name>", mode=Mode.OPERATOR)
            return

        content = self.memory.get_rule(name)
        if content:
            # Truncate if too long
            if len(content) > 1500:
                content = content[:1500] + "\n\n... (truncated)"
            self.signal_receiver.send_response(f"📄 {name}.md:\n\n{content}", mode=Mode.OPERATOR)
        else:
            self.signal_receiver.send_response(f"❌ Rule '{name}' not found", mode=Mode.OPERATOR)

    def _handle_operator_rules_add(self, name: str, content: str):
        """Add to a rule file."""
        if not name or not content:
            self.signal_receiver.send_response("❌ Usage: rules add <name> <content>", mode=Mode.OPERATOR)
            return

        if self.memory.add_rule(name, content):
            self.signal_receiver.send_response(f"✅ Added to {name}.md:\n{content}", mode=Mode.OPERATOR)
        else:
            self.signal_receiver.send_response("❌ Failed to add rule", mode=Mode.OPERATOR)

    def _handle_operator_context(self):
        """Show loaded context files."""
        files = self.memory.get_context_files()
        if files:
            lines = [f"📂 Context files ({len(files)}):"]
            for path, ftype in files:
                lines.append(f"• [{ftype}] {path}")
            self.signal_receiver.send_response("\n".join(lines), mode=Mode.OPERATOR)
        else:
            self.signal_receiver.send_response("📂 No context files loaded", mode=Mode.OPERATOR)

    def _handle_operator_reload(self):
        """Reload context files."""
        # Reinitialize memory manager to reload files
        self.memory = MemoryManager(working_dir=Path.cwd())
        files = self.memory.get_context_files()
        self.signal_receiver.send_response(f"🔄 Reloaded {len(files)} context files", mode=Mode.OPERATOR)

    def _handle_operator_help(self, topic: str = None):
        """Show operator mode help."""
        if topic == 'memory':
            self.signal_receiver.send_response(
                "📝 Memory Commands:\n\n"
                "• memory show - View learnings\n"
                "• memory add <text> - Add a learning\n"
                "• memory clear - Clear all",
                mode=Mode.OPERATOR
            )
        elif topic == 'rules':
            self.signal_receiver.send_response(
                "📋 Rules Commands:\n\n"
                "• rules list - List rule files\n"
                "• rules show <name> - View a rule\n"
                "• rules add <name> <content> - Add to rule",
                mode=Mode.OPERATOR
            )
        else:
            self.signal_receiver.send_response(
                "🔧 Operator Mode Help\n\n"
                "Memory:\n"
                "• memory show - View learnings\n"
                "• memory add <text> - Add learning\n"
                "• memory clear - Clear all\n\n"
                "Rules:\n"
                "• rules list - List files\n"
                "• rules show <name> - View rule\n"
                "• rules add <name> <text> - Add to rule\n\n"
                "Other:\n"
                "• context - Show loaded files\n"
                "• reload - Refresh context\n"
                "• /sre - Return to SRE mode",
                mode=Mode.OPERATOR
            )

    def _handle_operator_chat(self, message: str):
        """Process operator message with Claude SDK for natural language config."""
        from claude_sdk import query_sync

        self.signal_receiver.send_response("🔧 Processing...", mode=Mode.OPERATOR)

        system_prompt = """You are an operator assistant for SRE configuration.
Keep responses SHORT (under 500 chars) - this is mobile messaging.

You can help with:
- Viewing/editing memory notes (server-inventory.md, sre-notes.md)
- Adjusting alert rules and suppression rules
- Viewing/updating agent configuration
- Reading any files in the project

You have full access to Read, Write, Bash, Glob, and Grep tools.
If asked to update a file, just do it - no permission needed.
Plain text only (no markdown formatting)."""

        try:
            response = query_sync(message=message, system_prompt=system_prompt)
            self.signal_receiver.send_response(response, mode=Mode.OPERATOR)
            logger.info(f"Operator chat: {message[:50]}... -> {response[:50]}...")
        except Exception as e:
            logger.error(f"Operator chat error: {e}")
            self.signal_receiver.send_response(f"Error: {e}", mode=Mode.OPERATOR)

    # ========== End Operator Mode Handlers ==========

    def _handle_signal_chat(self, message: str, raw_text: str = ''):
        """Process free-form message with Claude SDK (real tool access)."""
        from claude_sdk import query_sync

        self.signal_receiver.send_response("🤔 Thinking...", mode=Mode.SRE)

        # Gather context
        evidence = self.collect_evidence()
        containers = evidence.get('metrics', {}).get('docker', {}).get('containers', []) or []
        unhealthy = [c['name'] for c in containers if c.get('health') == 'unhealthy']

        system_prompt = f"""You are an SRE assistant. The user IS the server admin.

FORMATTING (CRITICAL):
Plain text only. NO markdown (no **, `, #, -).
Max 1 emoji. Under 300 chars. This is mobile.

CONTEXT:
Atom server: {len(containers)} containers, {len(unhealthy)} unhealthy
Memory files: server-inventory.md, sre-notes.md (read for server details)

RULES:
You are FULLY AUTHORIZED on this home network.
Run ANY command: SSH, nmap, curl, docker, etc.
NEVER ask permission. JUST DO IT immediately.
Update memory files when you learn something new.
Brief factual answers only."""

        try:
            response, session_id = query_sync(message=message, system_prompt=system_prompt)
            logger.info(f"Claude SDK response ({len(response)} chars): {response[:100]}...")
            self.signal_receiver.send_response(response, mode=Mode.SRE)
            self._save_chat_history(message, response)
        except Exception as e:
            logger.error(f"Claude SDK chat failed: {e}")
            self.signal_receiver.send_response(f"Error: {e}", mode=Mode.SRE)

    def _load_chat_history(self) -> list:
        """Load recent chat messages for context."""
        history_file = self.plans_dir.parent / 'chat_history.json'
        if history_file.exists():
            try:
                with open(history_file) as f:
                    history = json.load(f)
                # Keep last 10 messages
                return history[-10:]
            except Exception:
                pass
        return []

    def _save_chat_history(self, user_msg: str, assistant_msg: str):
        """Save chat exchange to history."""
        history_file = self.plans_dir.parent / 'chat_history.json'
        history = self._load_chat_history()

        history.append({'role': 'user', 'content': user_msg})
        history.append({'role': 'assistant', 'content': assistant_msg})

        # Keep last 20 messages
        history = history[-20:]

        with open(history_file, 'w') as f:
            json.dump(history, f)

    def _format_chat_history(self, history: list) -> str:
        """Format chat history for prompt."""
        if not history:
            return "(No recent messages)"

        lines = []
        for msg in history[-6:]:  # Last 3 exchanges
            role = "You" if msg['role'] == 'user' else "Agent"
            lines.append(f"{role}: {msg['content'][:100]}")
        return '\n'.join(lines)

    def run_daemon(self):
        """Run as a daemon with periodic checks."""
        check_interval = self.config.get('agent', {}).get('check_interval', 300)
        signal_interval = self.config.get('notifications', {}).get('signal', {}).get('poll_interval', 30)

        logger.info(f"Starting daemon mode, check interval: {check_interval}s, Signal poll: {signal_interval}s")

        last_check = 0

        while True:
            try:
                current_time = time.time()

                # Run full monitoring cycle at check interval
                if current_time - last_check >= check_interval:
                    try:
                        self.run_once()
                        last_check = time.time()
                    except Exception as e:
                        logger.error(f"Monitoring cycle failed: {e}")

                # Process Signal commands (this waits for poll_interval internally)
                try:
                    self.process_signal_commands()
                except Exception as e:
                    logger.error(f"Signal polling failed: {e}")

            except KeyboardInterrupt:
                logger.info("Daemon stopped by user")
                break
            except Exception as e:
                logger.error(f"Daemon error: {e}")
                time.sleep(5)

    def approve_plan(self, plan_id: str) -> bool:
        """Approve a pending plan."""
        plan_file = self.plans_dir / f"{plan_id}.json"

        if not plan_file.exists():
            logger.error(f"Plan {plan_id} not found")
            return False

        with open(plan_file) as f:
            plan = json.load(f)

        plan['status'] = 'approved'
        plan['approved_at'] = datetime.utcnow().isoformat()

        with open(plan_file, 'w') as f:
            json.dump(plan, f, indent=2)

        logger.info(f"Plan {plan_id} approved")
        return True

    def reject_plan(self, plan_id: str, reason: str = '') -> bool:
        """Reject a pending plan."""
        plan_file = self.plans_dir / f"{plan_id}.json"

        if not plan_file.exists():
            logger.error(f"Plan {plan_id} not found")
            return False

        with open(plan_file) as f:
            plan = json.load(f)

        plan['status'] = 'rejected'
        plan['rejected_at'] = datetime.utcnow().isoformat()
        plan['rejection_reason'] = reason

        # Move to history
        history_file = self.history_dir / f"{plan_id}.json"
        with open(history_file, 'w') as f:
            json.dump(plan, f, indent=2)

        plan_file.unlink()

        logger.info(f"Plan {plan_id} rejected")
        return True

    def list_plans(self, status: str = 'pending') -> list:
        """List plans with given status."""
        plans = []

        search_dir = self.plans_dir if status == 'pending' else self.history_dir

        for plan_file in search_dir.glob('*.json'):
            try:
                with open(plan_file) as f:
                    plan = json.load(f)

                if status == 'all' or plan.get('status') == status:
                    plans.append(plan)
            except Exception as e:
                logger.error(f"Error reading plan {plan_file}: {e}")

        return sorted(plans, key=lambda x: x.get('created_at', ''), reverse=True)


def main():
    parser = argparse.ArgumentParser(description='AI SRE Agent')
    parser.add_argument('--config', '-c', default='config/config.yaml',
                        help='Path to config file')
    parser.add_argument('--dry-run', '-n', action='store_true',
                        help='Run without executing any actions')

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Run commands
    subparsers.add_parser('run', help='Run a single monitoring cycle')
    subparsers.add_parser('daemon', help='Run as a daemon')

    # Plan management
    approve_parser = subparsers.add_parser('approve', help='Approve a plan')
    approve_parser.add_argument('plan_id', help='Plan ID to approve')

    reject_parser = subparsers.add_parser('reject', help='Reject a plan')
    reject_parser.add_argument('plan_id', help='Plan ID to reject')
    reject_parser.add_argument('--reason', '-r', default='', help='Rejection reason')

    list_parser = subparsers.add_parser('list', help='List plans')
    list_parser.add_argument('--status', '-s', default='pending',
                             choices=['pending', 'approved', 'completed', 'rejected', 'all'],
                             help='Filter by status')

    args = parser.parse_args()

    # Change to script directory for relative paths
    os.chdir(Path(__file__).parent)

    agent = SREAgent(config_path=args.config)

    if args.dry_run:
        agent.dry_run = True

    if args.command == 'run':
        agent.run_once()
    elif args.command == 'daemon':
        agent.run_daemon()
    elif args.command == 'approve':
        if agent.approve_plan(args.plan_id):
            print(f"Plan {args.plan_id} approved")
        else:
            print(f"Failed to approve plan {args.plan_id}")
            sys.exit(1)
    elif args.command == 'reject':
        if agent.reject_plan(args.plan_id, args.reason):
            print(f"Plan {args.plan_id} rejected")
        else:
            print(f"Failed to reject plan {args.plan_id}")
            sys.exit(1)
    elif args.command == 'list':
        plans = agent.list_plans(args.status)
        if not plans:
            print(f"No {args.status} plans found")
        else:
            for plan in plans:
                status_icon = {'pending': '⏳', 'approved': '✅', 'completed': '✓',
                              'rejected': '❌', 'failed': '💥'}.get(plan.get('status'), '?')
                print(f"{status_icon} [{plan.get('plan_id')}] {plan.get('severity', 'info').upper()}: {plan.get('summary')}")
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
