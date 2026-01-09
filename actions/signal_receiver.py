"""Signal message receiver for processing approval commands."""

import asyncio
import json
import logging
import re
import urllib.request
import urllib.error
from typing import Optional

logger = logging.getLogger('ai-sre-agent.signal')

# Try to import websockets
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets library not available, Signal receiving disabled")


class SignalReceiver:
    """Polls Signal API for incoming messages and parses commands."""

    def __init__(self, config: dict):
        self.config = config
        self.api_url = config.get('signal', {}).get('api_url', '')
        self.sender = config.get('signal', {}).get('sender', '')
        self.recipient = config.get('signal', {}).get('recipient', '')
        self.poll_interval = config.get('signal', {}).get('poll_interval', 30)
        self.enabled = config.get('signal', {}).get('enabled', False)

        # Convert http:// to ws:// for websocket URL
        self.ws_url = self.api_url.replace('http://', 'ws://').replace('https://', 'wss://')

    def poll_messages(self) -> list:
        """
        Poll Signal API for incoming messages using websocket.

        Returns:
            List of parsed commands from messages
        """
        if not self.enabled or not self.api_url or not self.sender:
            return []

        if not WEBSOCKETS_AVAILABLE:
            return []

        try:
            # Run the async websocket receiver
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self._async_poll_messages())
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Failed to poll Signal messages: {e}")
            return []

    async def _async_poll_messages(self) -> list:
        """Async websocket polling for messages."""
        commands = []
        ws_endpoint = f"{self.ws_url}/v1/receive/{self.sender}"
        logger.info(f"Polling Signal messages for {self.poll_interval}s...")

        try:
            # Connect and wait for messages with longer timeout
            async with websockets.connect(ws_endpoint, close_timeout=5) as ws:
                # Wait for messages up to poll_interval seconds
                end_time = asyncio.get_event_loop().time() + self.poll_interval

                while asyncio.get_event_loop().time() < end_time:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        logger.info(f"Raw message received: {msg[:200]}...")
                        data = json.loads(msg)
                        cmd = self._parse_message(data)
                        if cmd:
                            logger.info(f"Parsed command: {cmd}")
                            commands.append(cmd)
                            # Got a command, return immediately to process it
                            break
                        else:
                            logger.info("Message received but no command parsed")
                    except asyncio.TimeoutError:
                        # No message yet, keep waiting
                        continue

        except asyncio.TimeoutError:
            pass  # No messages
        except Exception as e:
            logger.debug(f"Signal websocket: {e}")

        if commands:
            logger.info(f"Received {len(commands)} Signal commands")
        else:
            logger.info("Signal poll complete, no messages")

        return commands

    def _parse_message(self, msg: dict) -> Optional[dict]:
        """
        Parse a Signal message into a command.

        Args:
            msg: Raw message from Signal API

        Returns:
            Command dict or None if not a valid command
        """
        try:
            envelope = msg.get('envelope', {})
            # Log what type of message this is
            msg_types = [k for k in envelope.keys() if 'Message' in k or k in ['typingMessage', 'receiptMessage']]
            logger.info(f"Message types in envelope: {msg_types}")
            sender = envelope.get('source', '')
            timestamp = envelope.get('timestamp', 0)

            # Try to get message text from different structures
            text = ''

            # Regular incoming message
            data_message = envelope.get('dataMessage', {})
            if data_message:
                text = data_message.get('message', '')

            # Sync message (Note-to-Self or sent from linked device)
            sync_message = envelope.get('syncMessage', {})
            if sync_message and not text:
                sent_message = sync_message.get('sentMessage', {})
                if sent_message:
                    text = sent_message.get('message', '')
                    # For sync messages, the sender is the account owner
                    sender = envelope.get('source', self.recipient)

            # Only process messages from authorized sender
            if sender != self.recipient:
                logger.debug(f"Ignoring message from unauthorized sender: {sender}")
                return None

            if not text:
                logger.info(f"No text in message (types: {msg_types})")
                return None

            logger.info(f"Received Signal message: {text[:50]}...")

            # Parse the command
            cmd = self._parse_command(text)
            if cmd:
                cmd['sender'] = sender
                cmd['timestamp'] = timestamp
                cmd['raw_text'] = text

            return cmd

        except Exception as e:
            logger.error(f"Failed to parse message: {e}")
            return None

    def _parse_command(self, text: str) -> Optional[dict]:
        """
        Parse command text into structured command.

        Supported commands:
        - approve <plan_id> / yes <plan_id> / ok <plan_id>
        - reject <plan_id> / no <plan_id> / deny <plan_id>
        - status
        - help

        Args:
            text: Message text

        Returns:
            Command dict with 'action' and optional 'plan_id'
        """
        text = text.lower().strip()

        # Approve patterns
        approve_match = re.match(r'^(approve|yes|ok|execute|run)\s+(\S+)', text)
        if approve_match:
            return {
                'action': 'approve',
                'plan_id': approve_match.group(2)
            }

        # Just "approve" or "yes" without plan_id (approve most recent)
        if text in ['approve', 'yes', 'ok', 'execute', 'run']:
            return {
                'action': 'approve',
                'plan_id': None  # Will approve most recent pending plan
            }

        # Reject patterns
        reject_match = re.match(r'^(reject|no|deny|cancel|skip)\s+(\S+)', text)
        if reject_match:
            return {
                'action': 'reject',
                'plan_id': reject_match.group(2)
            }

        # Just "reject" or "no" without plan_id
        if text in ['reject', 'no', 'deny', 'cancel', 'skip']:
            return {
                'action': 'reject',
                'plan_id': None  # Will reject most recent pending plan
            }

        # Status command
        if text in ['status', 'pending', 'list', 'plans']:
            return {'action': 'status'}

        # Help command
        if text in ['help', '?', 'commands']:
            return {'action': 'help'}

        # Any other message - treat as chat for Claude
        logger.debug(f"Routing to chat: {text}")
        return {'action': 'chat', 'text': text}

    def send_response(self, message: str) -> bool:
        """
        Send a response message via Signal.

        Args:
            message: Response text to send

        Returns:
            True if sent successfully
        """
        if not self.enabled or not self.api_url:
            return False

        try:
            url = f"{self.api_url}/v1/send"

            data = {
                "number": self.sender,
                "recipients": [self.recipient],
                "message": message
            }

            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=15) as response:
                return response.status in [200, 201]

        except Exception as e:
            logger.error(f"Failed to send Signal response: {e}")
            return False

    def send_help(self) -> bool:
        """Send help message with available commands."""
        help_text = """🤖 SRE Agent Commands:

• approve [plan_id] - Execute a pending plan
• reject [plan_id] - Dismiss a pending plan
• status - List all pending plans
• help - Show this message

If plan_id is omitted, the most recent pending plan is used.

Shortcuts: yes/ok (approve), no/deny (reject)"""

        return self.send_response(help_text)

    def send_status(self, plans: list) -> bool:
        """
        Send status message with pending plans.

        Args:
            plans: List of pending plan dicts
        """
        if not plans:
            return self.send_response("📋 No pending plans.")

        lines = ["📋 Pending Plans:\n"]
        for plan in plans[:5]:  # Limit to 5 most recent
            plan_id = plan.get('plan_id', 'unknown')
            severity = plan.get('severity', 'info').upper()
            summary = plan.get('summary', 'Unknown')[:50]
            lines.append(f"• [{severity}] {plan_id}\n  {summary}")

        if len(plans) > 5:
            lines.append(f"\n... and {len(plans) - 5} more")

        lines.append("\nReply: approve <id> or reject <id>")

        return self.send_response("\n".join(lines))
