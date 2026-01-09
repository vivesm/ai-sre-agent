"""Notification handlers for email, mobile push, and Signal."""

import json
import logging
import os
import subprocess
from typing import Optional
import urllib.request
import urllib.error

logger = logging.getLogger('ai-sre-agent.notify')


class Notifier:
    """Sends notifications via email, Home Assistant, and Signal."""

    def __init__(self, config: dict):
        self.config = config

        # Email config
        self.email_enabled = config.get('email', {}).get('enabled', True)
        self.smtp_host = config.get('email', {}).get('smtp_host', 'localhost')
        self.smtp_port = config.get('email', {}).get('smtp_port', 2525)
        self.email_from = config.get('email', {}).get('from', 'alerts@vives.io')
        self.email_to = config.get('email', {}).get('to', '')

        # Home Assistant config
        self.push_enabled = config.get('mobile_push', {}).get('enabled', True)
        self.ha_url = config.get('mobile_push', {}).get('ha_url', '')
        self.ha_token_env = config.get('mobile_push', {}).get('ha_token_env', 'HA_TOKEN')
        self.ha_device = config.get('mobile_push', {}).get('device', '')

        # TTS config
        self.tts_enabled = config.get('tts', {}).get('enabled', False)
        self.tts_entity = config.get('tts', {}).get('entity', '')

        # Signal config
        self.signal_enabled = config.get('signal', {}).get('enabled', False)
        self.signal_api_url = config.get('signal', {}).get('api_url', '')
        self.signal_sender = config.get('signal', {}).get('sender', '')
        self.signal_recipient = config.get('signal', {}).get('recipient', '')

    def send_plan_notification(self, plan: dict) -> bool:
        """Send notification about a new pending plan."""
        plan_id = plan.get('plan_id', 'unknown')
        summary = plan.get('summary', 'Unknown issue')
        severity = plan.get('severity', 'info')
        confidence = plan.get('confidence', 0)

        # Build message
        title = f"[{severity.upper()}] SRE Alert: {summary}"

        steps_text = ""
        for step in plan.get('plan', []):
            steps_text += f"  {step.get('step')}. {step.get('action')}\n"

        body = f"""
Plan ID: {plan_id}
Severity: {severity}
Confidence: {confidence:.0%}

Root Cause: {plan.get('root_cause', 'Unknown')}

Proposed Actions:
{steps_text}
Risk: {plan.get('risk', 'unknown')}

To approve: sre-agent approve {plan_id}
To reject: sre-agent reject {plan_id}
"""

        success = True

        # Build Signal-friendly message with reply instructions
        signal_message = f"""🔔 {title}

Plan ID: {plan_id}
Severity: {severity}
Confidence: {confidence:.0%}

Root Cause: {plan.get('root_cause', 'Unknown')}

Proposed Actions:
{steps_text}
Risk: {plan.get('risk', 'unknown')}

Reply with:
• "approve {plan_id}" to execute
• "reject {plan_id}" to dismiss
• "status" to see all pending plans"""

        # Send based on severity
        if severity == 'critical':
            # All channels for critical
            if self.signal_enabled:
                success &= self._send_signal(signal_message, plan_id)
            if self.push_enabled:
                success &= self._send_mobile_push(title, body, plan_id, priority='high')
            if self.email_enabled:
                success &= self._send_email(title, body)
            if self.tts_enabled:
                self._send_tts(f"Critical SRE alert: {summary}")
        elif severity == 'warning':
            # Signal + Mobile push for warnings
            if self.signal_enabled:
                success &= self._send_signal(signal_message, plan_id)
            if self.push_enabled:
                success &= self._send_mobile_push(title, body, plan_id)
        else:
            # Email only for info
            if self.email_enabled:
                success &= self._send_email(title, body)

        return success

    def send_result_notification(self, plan: dict) -> bool:
        """Send notification about plan execution result."""
        plan_id = plan.get('plan_id', 'unknown')
        status = plan.get('status', 'unknown')
        summary = plan.get('summary', 'Unknown issue')
        result = plan.get('result', {})

        if status == 'completed':
            title = f"[FIXED] {summary}"
            body = f"Plan {plan_id} executed successfully."
            signal_message = f"✅ {title}\n\nPlan {plan_id} executed successfully."
        else:
            title = f"[FAILED] {summary}"
            body = f"Plan {plan_id} failed.\n\nError: {result.get('error', 'Unknown')}"
            signal_message = f"❌ {title}\n\nPlan {plan_id} failed.\nError: {result.get('error', 'Unknown')}"

        success = True

        # Signal is preferred for quick feedback
        if self.signal_enabled:
            success &= self._send_signal(signal_message, plan_id)
        if self.push_enabled:
            success &= self._send_mobile_push(title, body, plan_id)
        elif self.email_enabled:
            success &= self._send_email(title, body)

        return success

    def _send_email(self, subject: str, body: str) -> bool:
        """Send email via SMTP relay."""
        if not self.email_to:
            logger.warning("Email recipient not configured")
            return False

        try:
            # Use the existing send-email.sh script if available
            script_path = '/home/melvin/server/scripts/lib/send-email.sh'
            if os.path.exists(script_path):
                result = subprocess.run(
                    [script_path, self.email_to, subject, body],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                return result.returncode == 0

            # Fallback: direct Python email
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg['Subject'] = subject
            msg['From'] = self.email_from
            msg['To'] = self.email_to
            msg.set_content(body)

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                server.send_message(msg)

            logger.info(f"Email sent: {subject}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False

    def _send_mobile_push(self, title: str, message: str, plan_id: str,
                          priority: str = 'normal') -> bool:
        """Send mobile push notification via Home Assistant."""
        if not self.ha_url or not self.ha_device:
            logger.warning("Home Assistant push not configured")
            return False

        ha_token = os.environ.get(self.ha_token_env, '')
        if not ha_token:
            logger.warning(f"HA token not found in {self.ha_token_env}")
            return False

        try:
            url = f"{self.ha_url}/api/services/notify/{self.ha_device}"

            # Build notification data with actionable buttons
            data = {
                'title': title,
                'message': message[:1000],  # Truncate long messages
                'data': {
                    'priority': priority,
                    'tag': f'sre-plan-{plan_id}',
                    'actions': [
                        {
                            'action': f'SRE_APPROVE_{plan_id}',
                            'title': 'Approve'
                        },
                        {
                            'action': f'SRE_REJECT_{plan_id}',
                            'title': 'Reject'
                        }
                    ]
                }
            }

            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers={
                    'Authorization': f'Bearer {ha_token}',
                    'Content-Type': 'application/json'
                },
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    logger.info(f"Mobile push sent: {title}")
                    return True
                else:
                    logger.error(f"Mobile push failed: {response.status}")
                    return False

        except urllib.error.HTTPError as e:
            logger.error(f"Mobile push HTTP error: {e.code} - {e.reason}")
            return False
        except Exception as e:
            logger.error(f"Failed to send mobile push: {e}")
            return False

    def _send_tts(self, message: str) -> bool:
        """Send TTS announcement via Home Assistant."""
        if not self.ha_url or not self.tts_entity:
            return False

        ha_token = os.environ.get(self.ha_token_env, '')
        if not ha_token:
            return False

        try:
            url = f"{self.ha_url}/api/services/tts/google_translate_say"

            data = {
                'entity_id': self.tts_entity,
                'message': message
            }

            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers={
                    'Authorization': f'Bearer {ha_token}',
                    'Content-Type': 'application/json'
                },
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status == 200

        except Exception as e:
            logger.error(f"Failed to send TTS: {e}")
            return False

    def _send_signal(self, message: str, plan_id: str = None) -> bool:
        """Send Signal message via REST API."""
        if not self.signal_api_url or not self.signal_sender or not self.signal_recipient:
            logger.warning("Signal not configured")
            return False

        try:
            url = f"{self.signal_api_url}/v1/send"

            data = {
                "number": self.signal_sender,
                "recipients": [self.signal_recipient],
                "message": message
            }

            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=15) as response:
                if response.status in [200, 201]:
                    logger.info(f"Signal message sent: {message[:50]}...")
                    return True
                else:
                    logger.error(f"Signal send failed: {response.status}")
                    return False

        except urllib.error.HTTPError as e:
            logger.error(f"Signal HTTP error: {e.code} - {e.reason}")
            return False
        except urllib.error.URLError as e:
            logger.error(f"Signal connection error: {e.reason}")
            return False
        except Exception as e:
            logger.error(f"Failed to send Signal message: {e}")
            return False
