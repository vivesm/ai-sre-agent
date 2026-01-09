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

    def analyze_and_plan(self, evidence: dict) -> Optional[dict]:
        """Send evidence to Claude for analysis and plan generation."""
        if not evidence.get('issues'):
            logger.info("No issues detected, skipping analysis")
            return None

        logger.info(f"Analyzing {len(evidence['issues'])} issues...")

        try:
            plan = self.analyzer.analyze(evidence)

            if plan and plan.get('plan'):
                # Save plan to file
                plan_id = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
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
            return self.notifier.send_plan_notification(plan)
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

        # 2. Check for existing approved plans first
        approved_plans = self.check_approvals()
        for plan in approved_plans:
            self.execute_plan(plan)

        # 3. Analyze new issues and create plans
        if evidence.get('issues'):
            plan = self.analyze_and_plan(evidence)
            if plan:
                self.notify_user(plan)

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

            if action == 'approve':
                self._handle_signal_approve(plan_id)
            elif action == 'reject':
                self._handle_signal_reject(plan_id)
            elif action == 'status':
                plans = self.list_plans('pending')
                self.signal_receiver.send_status(plans)
            elif action == 'help':
                self.signal_receiver.send_help()
            elif action == 'chat':
                self._handle_signal_chat(cmd.get('text', ''), cmd.get('raw_text', ''))

    def _handle_signal_approve(self, plan_id: str = None):
        """Handle approve command from Signal."""
        plans = self.list_plans('pending')

        if not plans:
            self.signal_receiver.send_response("📭 No pending plans to approve.")
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
                    f"Available plans: {', '.join(p.get('plan_id', '')[:8] for p in plans[:5])}"
                )
                return
        else:
            # Use most recent pending plan
            target_plan = plans[0]

        # Approve the plan
        pid = target_plan.get('plan_id')
        if self.approve_plan(pid):
            self.signal_receiver.send_response(
                f"✅ Plan {pid} approved.\n\n"
                f"Executing: {target_plan.get('summary', 'Unknown')}"
            )
            # Execute immediately
            result = self.execute_plan(target_plan)
            if result.get('success'):
                self.signal_receiver.send_response(f"🎉 Plan {pid} executed successfully!")
            else:
                self.signal_receiver.send_response(
                    f"❌ Plan {pid} execution failed.\n\n"
                    f"Error: {result.get('error', 'Unknown')}"
                )
        else:
            self.signal_receiver.send_response(f"❌ Failed to approve plan {pid}")

    def _handle_signal_reject(self, plan_id: str = None):
        """Handle reject command from Signal."""
        plans = self.list_plans('pending')

        if not plans:
            self.signal_receiver.send_response("📭 No pending plans to reject.")
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
                    f"Available plans: {', '.join(p.get('plan_id', '')[:8] for p in plans[:5])}"
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
                f"Dismissed: {target_plan.get('summary', 'Unknown')}"
            )
        else:
            self.signal_receiver.send_response(f"❌ Failed to reject plan {pid}")

    def _handle_signal_chat(self, message: str, raw_text: str = ''):
        """Process free-form message with Claude."""
        import subprocess

        # Send acknowledgment for potentially slow operations
        self.signal_receiver.send_response("🤔 Thinking...")

        # Gather current system context
        evidence = self.collect_evidence()
        pending_plans = self.list_plans('pending')

        # Load recent chat history for context
        chat_history = self._load_chat_history()

        # Build context-rich prompt
        containers = evidence.get('metrics', {}).get('docker', {}).get('containers', []) or []
        unhealthy = [c['name'] for c in containers if c.get('health') == 'unhealthy']
        disk_info = evidence.get('metrics', {}).get('system', {}).get('disk', []) or []

        prompt = f"""You are an SRE assistant responding via Signal message to a home server admin.
Keep responses SHORT (under 500 chars) - this is mobile messaging.

## Current System State
- Server: Atom (Ubuntu, Docker host)
- Containers: {len(containers)} total, {len(unhealthy)} unhealthy
- Unhealthy: {', '.join(unhealthy) if unhealthy else 'None'}
- Issues detected: {len(evidence.get('issues', []))}
- Pending plans awaiting approval: {len(pending_plans)}
- Disk usage: {', '.join(f"{d['mount']}: {d['percent']}%" for d in disk_info[:3])}

## Recent Conversation
{self._format_chat_history(chat_history)}

## User Message
{message}

## Instructions
1. If asking about system status → give brief, factual answer
2. If requesting an action (restart, check, etc.) → describe what you'd do and say "Reply 'yes' to proceed"
3. If you need to create a remediation plan → say "I'll create a plan for your approval"
4. Be conversational but concise. Use emojis sparingly.
5. If unsure, ask a clarifying question.

Respond in plain text only."""

        try:
            result = subprocess.run(
                ['claude', '-p', prompt, '--output-format', 'text'],
                capture_output=True,
                text=True,
                timeout=90
            )

            if result.returncode == 0:
                response = result.stdout.strip()[:1500]
                self.signal_receiver.send_response(response)
                # Save to history
                self._save_chat_history(message, response)
            else:
                logger.error(f"Claude chat failed: {result.stderr}")
                self.signal_receiver.send_response(
                    "❌ Couldn't process that. Try:\n"
                    "• status - system overview\n"
                    "• help - all commands"
                )

        except subprocess.TimeoutExpired:
            self.signal_receiver.send_response("⏱️ Taking too long. Try a simpler question?")
        except Exception as e:
            logger.error(f"Signal chat failed: {e}")
            self.signal_receiver.send_response("❌ Error. Try: status, help")

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
