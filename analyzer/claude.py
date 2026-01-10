"""Claude Code CLI analyzer for issue analysis and plan generation."""

import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger('ai-sre-agent.analyzer')

# System prompt for the SRE assistant
SYSTEM_PROMPT = """You are an SRE assistant operating in plan-first mode.
Do NOT execute anything. Produce a proposal only.

Context:
- Environment: single-host Linux server named "Atom"
- Orchestrator: Docker (non-K8s)
- Human approval required before execution
- Safety over speed

Instructions:
1. Analyze only the provided evidence.
2. If evidence is insufficient, say so and request specific missing data.
3. Prefer minimal, reversible actions.
4. Assume dry-run unless explicitly approved later.

Output JSON ONLY. No prose. Conform exactly to this schema:

{
  "plan_schema_version": "1.0",
  "summary": "<1 sentence>",
  "severity": "info|warning|critical",
  "confidence": 0.0,
  "root_cause": "<concise, evidence-based>",
  "evidence": [
    "<key log line, metric, or fact>"
  ],
  "risk": "low|medium|high",
  "requires_approval": true,
  "prechecks": [
    "<command or check to validate assumptions>"
  ],
  "plan": [
    {
      "step": 1,
      "action": "<what>",
      "command": "<exact command or null>",
      "timeout_seconds": 60,
      "reversible": true
    }
  ],
  "postchecks": [
    "<how success is verified>"
  ],
  "rollback": [
    "<how to undo each step>"
  ],
  "do_not_execute_if": [
    "<explicit safety stop conditions>"
  ],
  "notes": "<optional constraints or alternatives>"
}

Confidence scoring guidance (apply internally):
- Known playbook match: +0.4
- Read-only or restart-only actions: +0.2
- Has rollback + postchecks: +0.2
- Touches data volumes or deletes data: cap confidence at 0.3

If no safe plan exists, return an empty "plan" array and explain why in "notes"."""


class ClaudeAnalyzer:
    """Analyzes issues using Claude Code CLI."""

    def __init__(self, config: dict):
        self.config = config
        # Use full path since NVM isn't loaded in daemon mode
        default_claude = '/home/melvin/.nvm/versions/node/v20.19.6/bin/claude'
        self.command = config.get('command', default_claude)
        self.timeout = config.get('timeout', 120)
        self.model = config.get('model', None)  # Use default

    def analyze(self, evidence: dict) -> Optional[dict]:
        """
        Send evidence to Claude Code CLI for analysis.

        Args:
            evidence: Dict containing issues and metrics from collectors

        Returns:
            Plan dict or None if no actionable plan
        """
        # Format evidence for the prompt
        prompt = self._format_prompt(evidence)

        try:
            # Build command
            cmd = [self.command, '-p', prompt, '--output-format', 'json']

            # Add model if specified
            if self.model:
                cmd.extend(['--model', self.model])

            logger.debug(f"Running Claude CLI: {' '.join(cmd[:3])}...")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=None  # Use current environment (inherits Claude auth)
            )

            if result.returncode != 0:
                logger.error(f"Claude CLI failed: {result.stderr}")
                return None

            # Parse JSON response
            raw_response = result.stdout.strip()

            logger.debug(f"Raw Claude response (first 500 chars): {raw_response[:500]}")

            # Handle Claude CLI JSON envelope (--output-format json wraps response)
            response = raw_response
            try:
                envelope = json.loads(raw_response)
                if isinstance(envelope, dict) and 'result' in envelope:
                    # Extract the actual response from the envelope
                    response = envelope['result']
                    logger.debug(f"Extracted result from envelope: {response[:200]}")
            except json.JSONDecodeError:
                pass

            # Try multiple strategies to extract JSON
            plan = None

            # Strategy 1: Direct JSON parse (in case response is clean JSON)
            try:
                plan = json.loads(response)
            except json.JSONDecodeError:
                pass

            # Strategy 2: Extract from markdown code blocks
            if plan is None and '```json' in response:
                try:
                    start = response.find('```json') + 7
                    end = response.find('```', start)
                    json_str = response[start:end].strip()
                    plan = json.loads(json_str)
                except (json.JSONDecodeError, ValueError):
                    pass

            # Strategy 3: Generic code block
            if plan is None and '```' in response:
                try:
                    start = response.find('```') + 3
                    # Skip language identifier if present
                    newline = response.find('\n', start)
                    if newline != -1 and newline - start < 20:
                        start = newline + 1
                    end = response.find('```', start)
                    json_str = response[start:end].strip()
                    plan = json.loads(json_str)
                except (json.JSONDecodeError, ValueError):
                    pass

            # Strategy 4: Find JSON object boundaries
            if plan is None:
                try:
                    # Find first { and last }
                    start = response.find('{')
                    end = response.rfind('}')
                    if start != -1 and end != -1 and end > start:
                        json_str = response[start:end + 1]
                        plan = json.loads(json_str)
                except (json.JSONDecodeError, ValueError):
                    pass

            if plan is None:
                logger.error(f"Could not extract JSON from response")
                logger.debug(f"Full response: {response}")
                return None

            # Validate required fields
            if not self._validate_plan(plan):
                logger.warning("Plan validation failed")
                return None

            return plan

        except subprocess.TimeoutExpired:
            logger.error(f"Claude CLI timed out after {self.timeout}s")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response: {e}")
            logger.debug(f"Raw response: {result.stdout[:500] if 'result' in locals() else 'N/A'}")
            return None
        except FileNotFoundError:
            logger.error(f"Claude CLI not found: {self.command}")
            return None
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return None

    def _format_prompt(self, evidence: dict) -> str:
        """Format evidence into a prompt for Claude."""
        # Start with the system prompt context
        prompt_parts = [
            SYSTEM_PROMPT,
            "\n\n---\n\nEvidence:\n",
            json.dumps(evidence, indent=2, default=str)
        ]

        return ''.join(prompt_parts)

    def _validate_plan(self, plan: dict) -> bool:
        """Validate that the plan has required fields."""
        required_fields = [
            'plan_schema_version',
            'summary',
            'severity',
            'confidence',
            'plan'
        ]

        for field in required_fields:
            if field not in plan:
                logger.warning(f"Plan missing required field: {field}")
                return False

        # Validate severity
        if plan.get('severity') not in ['info', 'warning', 'critical']:
            logger.warning(f"Invalid severity: {plan.get('severity')}")
            return False

        # Validate confidence
        confidence = plan.get('confidence', 0)
        if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
            logger.warning(f"Invalid confidence: {confidence}")
            return False

        # Validate plan steps if present
        for step in plan.get('plan', []):
            if 'step' not in step or 'action' not in step:
                logger.warning("Plan step missing required fields")
                return False

        return True


class FallbackAnalyzer:
    """Simple rule-based fallback analyzer when Claude is unavailable."""

    def __init__(self):
        self.playbooks = self._load_playbooks()

    def _load_playbooks(self) -> dict:
        """Load playbook rules."""
        return {
            'container_unhealthy': {
                'summary': 'Container is unhealthy',
                'severity': 'warning',
                'confidence': 0.6,
                'risk': 'low',
                'plan': [
                    {
                        'step': 1,
                        'action': 'Restart container',
                        'command': 'docker restart {container}',
                        'timeout_seconds': 60,
                        'reversible': True
                    }
                ],
                'rollback': ['Container will auto-restart if configured'],
                'postchecks': ['docker ps --filter name={container}']
            },
            'disk_space_low': {
                'summary': 'Disk space is low',
                'severity': 'warning',
                'confidence': 0.5,
                'risk': 'low',
                'plan': [
                    {
                        'step': 1,
                        'action': 'Clean Docker system',
                        'command': 'docker system prune -f',
                        'timeout_seconds': 120,
                        'reversible': False
                    }
                ],
                'rollback': ['Cannot recover pruned images'],
                'postchecks': ['df -h {mount}']
            }
        }

    def analyze(self, evidence: dict) -> Optional[dict]:
        """Generate a plan based on simple rules."""
        issues = evidence.get('issues', [])

        if not issues:
            return None

        # Find first matching playbook
        for issue in issues:
            issue_type = issue.get('type', '')
            if issue_type in self.playbooks:
                playbook = self.playbooks[issue_type].copy()

                # Substitute variables
                playbook['summary'] = f"{issue.get('message', playbook['summary'])}"
                playbook['evidence'] = [str(issue)]
                playbook['root_cause'] = issue.get('message', 'Unknown')
                playbook['plan_schema_version'] = '1.0'
                playbook['requires_approval'] = True
                playbook['prechecks'] = []
                playbook['do_not_execute_if'] = []
                playbook['notes'] = 'Generated by fallback analyzer (Claude unavailable)'

                # Substitute container name in commands
                container = issue.get('container', '')
                mount = issue.get('mount', '/')
                for step in playbook.get('plan', []):
                    if step.get('command'):
                        step['command'] = step['command'].format(
                            container=container,
                            mount=mount
                        )
                for i, check in enumerate(playbook.get('postchecks', [])):
                    playbook['postchecks'][i] = check.format(
                        container=container,
                        mount=mount
                    )

                return playbook

        return None
