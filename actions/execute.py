"""Safe execution engine for approved plans."""

import logging
import subprocess
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger('ai-sre-agent.execute')


class Executor:
    """Executes approved plans with safety guards."""

    def __init__(self, config: dict):
        self.config = config
        self.max_fixes_per_hour = config.get('max_auto_fixes_per_hour', 3)
        self.never_restart = set(config.get('never_restart', []))
        self.require_approval = set(config.get('require_approval', []))

        # Track executions for rate limiting
        self.execution_history = []

    def execute(self, plan: dict) -> dict:
        """
        Execute an approved plan.

        Args:
            plan: The approved plan dict

        Returns:
            Result dict with success status and details
        """
        result = {
            'success': True,
            'steps': [],
            'started_at': datetime.utcnow().isoformat(),
            'completed_at': None,
            'error': None
        }

        # Check rate limit
        if not self._check_rate_limit():
            result['success'] = False
            result['error'] = 'Rate limit exceeded'
            logger.warning("Execution rate limit exceeded")
            return result

        # Check safety conditions
        safety_check = self._check_safety(plan)
        if not safety_check['safe']:
            result['success'] = False
            result['error'] = f"Safety check failed: {safety_check['reason']}"
            logger.warning(f"Safety check failed: {safety_check['reason']}")
            return result

        # Run prechecks
        for precheck in plan.get('prechecks', []):
            precheck_result = self._run_command(precheck, timeout=30)
            if not precheck_result['success']:
                result['success'] = False
                result['error'] = f"Precheck failed: {precheck}"
                result['steps'].append({
                    'type': 'precheck',
                    'command': precheck,
                    'result': precheck_result
                })
                return result

        # Execute plan steps
        for step in plan.get('plan', []):
            step_num = step.get('step', 0)
            action = step.get('action', '')
            command = step.get('command')
            timeout = step.get('timeout_seconds', 60)

            logger.info(f"Executing step {step_num}: {action}")

            step_result = {
                'step': step_num,
                'action': action,
                'command': command,
                'success': True,
                'output': None,
                'error': None
            }

            if command:
                cmd_result = self._run_command(command, timeout=timeout)
                step_result['success'] = cmd_result['success']
                step_result['output'] = cmd_result['output']
                step_result['error'] = cmd_result['error']

                if not cmd_result['success']:
                    result['success'] = False
                    result['error'] = f"Step {step_num} failed: {cmd_result['error']}"
                    result['steps'].append(step_result)

                    # Attempt rollback
                    self._rollback(plan, result['steps'])
                    break

            result['steps'].append(step_result)

        # Run postchecks if all steps succeeded
        if result['success']:
            for postcheck in plan.get('postchecks', []):
                postcheck_result = self._run_command(postcheck, timeout=30)
                result['steps'].append({
                    'type': 'postcheck',
                    'command': postcheck,
                    'result': postcheck_result
                })

                if not postcheck_result['success']:
                    logger.warning(f"Postcheck failed: {postcheck}")
                    # Don't fail the whole plan for postcheck failure
                    # but log it

        result['completed_at'] = datetime.utcnow().isoformat()

        # Record execution for rate limiting
        self.execution_history.append(datetime.utcnow())

        return result

    def _check_rate_limit(self) -> bool:
        """Check if we're within the rate limit."""
        one_hour_ago = datetime.utcnow().timestamp() - 3600

        # Clean old entries
        self.execution_history = [
            t for t in self.execution_history
            if t.timestamp() > one_hour_ago
        ]

        return len(self.execution_history) < self.max_fixes_per_hour

    def _check_safety(self, plan: dict) -> dict:
        """Check safety conditions before execution."""
        # Check do_not_execute_if conditions
        for condition in plan.get('do_not_execute_if', []):
            # For now, these are informational - could be enhanced with actual checks
            logger.debug(f"Safety condition (unchecked): {condition}")

        # Check if any commands target protected containers
        for step in plan.get('plan', []):
            command = step.get('command', '')

            # Check for protected containers
            for container in self.never_restart:
                if container in command and ('restart' in command or 'stop' in command):
                    return {
                        'safe': False,
                        'reason': f"Command targets protected container: {container}"
                    }

            # Check for dangerous operations
            dangerous_patterns = [
                'rm -rf /',
                'rm -rf /*',
                'mkfs',
                '> /dev/',
                'dd if=',
                ':(){:|:&};:',
                'chmod -R 777 /',
            ]

            for pattern in dangerous_patterns:
                if pattern in command:
                    return {
                        'safe': False,
                        'reason': f"Dangerous command pattern detected: {pattern}"
                    }

        return {'safe': True, 'reason': None}

    def _run_command(self, command: str, timeout: int = 60) -> dict:
        """Run a shell command safely."""
        result = {
            'success': False,
            'output': None,
            'error': None,
            'return_code': None
        }

        try:
            logger.debug(f"Running: {command}")

            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            result['return_code'] = proc.returncode
            result['output'] = proc.stdout[:5000]  # Truncate large output
            result['success'] = proc.returncode == 0

            if proc.returncode != 0:
                result['error'] = proc.stderr[:1000]

            return result

        except subprocess.TimeoutExpired:
            result['error'] = f"Command timed out after {timeout}s"
            logger.error(f"Command timed out: {command}")
            return result
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Command failed: {e}")
            return result

    def _rollback(self, plan: dict, executed_steps: list):
        """Attempt to rollback executed steps."""
        rollback_instructions = plan.get('rollback', [])

        if not rollback_instructions:
            logger.warning("No rollback instructions available")
            return

        logger.info("Attempting rollback...")

        for instruction in rollback_instructions:
            logger.info(f"Rollback: {instruction}")
            # For now, just log rollback instructions
            # Could be enhanced to actually execute rollback commands
