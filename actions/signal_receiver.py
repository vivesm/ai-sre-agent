"""Signal message receiver for processing approval commands."""

import asyncio
import json
import logging
import re
import urllib.request
import urllib.error
from typing import Optional

from modes import Mode

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

                        # Handle potentially corrupted JSON (signal-cli-rest-api bug #680)
                        try:
                            data = json.loads(msg)
                        except json.JSONDecodeError:
                            # Try to extract JSON portion before any traceback
                            if '{' in msg and '}' in msg:
                                json_part = msg[:msg.rfind('}')+1]
                                try:
                                    data = json.loads(json_part)
                                    logger.warning("Recovered from corrupted JSON in Signal message")
                                except json.JSONDecodeError:
                                    logger.error(f"Failed to parse Signal message: {msg[:100]}...")
                                    continue
                            else:
                                logger.error(f"Invalid Signal message format: {msg[:100]}...")
                                continue

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

            # Check for reaction messages first (in syncMessage.sentMessage.reaction)
            sync_message = envelope.get('syncMessage', {})
            sent_message = sync_message.get('sentMessage', {})
            reaction = sent_message.get('reaction')

            if reaction and not reaction.get('isRemove', False):
                emoji = reaction.get('emoji', '')
                target_timestamp = reaction.get('targetSentTimestamp')
                logger.info(f"Reaction received: {emoji} on message {target_timestamp}")

                # Only process reactions from authorized sender
                if sender != self.recipient:
                    logger.debug(f"Ignoring reaction from unauthorized sender: {sender}")
                    return None

                return {
                    'action': 'reaction',
                    'emoji': emoji,
                    'target_timestamp': target_timestamp,
                    'sender': sender,
                    'timestamp': timestamp
                }

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

        All commands work in unified SRE mode:
        - Alert commands: approve, reject, status
        - Memory commands: memory show/add/clear
        - Rules commands: rules list/show/add
        - System commands: context, reload, help
        - Natural language: routed to Claude chat

        Args:
            text: Message text

        Returns:
            Command dict with 'action' and optional params
        """
        text_orig = text.strip()
        text_lower = text_orig.lower()
        parts = text_orig.split(maxsplit=2)
        cmd = parts[0].lower() if parts else ''

        # Approve patterns
        approve_match = re.match(r'^(approve|yes|ok|execute|run)\s+(\S+)', text_lower)
        if approve_match:
            return {'action': 'approve', 'plan_id': approve_match.group(2)}

        if text_lower in ['approve', 'yes', 'ok', 'execute', 'run']:
            return {'action': 'approve', 'plan_id': None}

        # Reject patterns
        reject_match = re.match(r'^(reject|no|deny|cancel|skip)\s+(\S+)', text_lower)
        if reject_match:
            return {'action': 'reject', 'plan_id': reject_match.group(2)}

        if text_lower in ['reject', 'no', 'deny', 'cancel', 'skip']:
            return {'action': 'reject', 'plan_id': None}

        # Status command
        if text_lower in ['status', 'pending', 'list', 'plans', '?']:
            return {'action': 'status'}

        # Memory commands
        if cmd == 'memory':
            subcmd = parts[1].lower() if len(parts) > 1 else 'show'
            if subcmd == 'show':
                return {'action': 'memory_show'}
            elif subcmd == 'add' and len(parts) > 2:
                return {'action': 'memory_add', 'text': parts[2]}
            elif subcmd == 'clear':
                return {'action': 'memory_clear'}
            else:
                return {'action': 'help', 'topic': 'memory'}

        # Rules commands
        if cmd == 'rules':
            subcmd = parts[1].lower() if len(parts) > 1 else 'list'
            if subcmd == 'list':
                return {'action': 'rules_list'}
            elif subcmd == 'show' and len(parts) > 2:
                return {'action': 'rules_show', 'name': parts[2]}
            elif subcmd == 'add' and len(parts) > 2:
                rest = text_orig[len('rules add '):].strip()
                name_parts = rest.split(maxsplit=1)
                if len(name_parts) == 2:
                    return {'action': 'rules_add', 'name': name_parts[0], 'content': name_parts[1]}
            return {'action': 'help', 'topic': 'rules'}

        # Context command
        if cmd == 'context':
            return {'action': 'context'}

        # Reload command
        if cmd == 'reload':
            return {'action': 'reload'}

        # Help command
        if cmd in ['help', 'commands']:
            return {'action': 'help'}

        # Natural language - route to Claude chat
        logger.debug(f"Routing to chat: {text_lower}")
        return {'action': 'chat', 'text': text_lower}

    def send_response(self, message: str, mode: Mode = None) -> bool:
        """
        Send a response message via Signal with optional mode prefix.

        Args:
            message: Response text to send
            mode: Optional mode to prefix message with

        Returns:
            True if sent successfully
        """
        if not self.enabled or not self.api_url:
            return False

        # Add mode prefix if specified
        if mode:
            message = f"[{mode.value}] {message}"

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
                success = response.status in [200, 201]
                if success:
                    logger.info(f"Signal response sent: {message[:50]}...")
                return success

        except Exception as e:
            logger.error(f"Failed to send Signal response: {e}")
            return False

    def send_help(self, mode: Mode = None) -> bool:
        """Send help message with available commands."""
        help_text = """🤖 SRE Agent Commands:

Alerts:
• approve [id] - Execute plan
• reject [id] - Dismiss plan
• status - List pending plans

Memory:
• memory show - Display memory
• memory add <text> - Add note
• memory clear - Clear memory

Rules:
• rules list - List rules
• rules show <name> - View rule
• rules add <name> <text> - Add to rule

System:
• context - Show loaded files
• reload - Refresh context
• help - Show this message

Reactions: 👍 approve, 👎 reject, 🔍 reinvestigate

Just chat naturally for device control or questions!"""

        return self.send_response(help_text, mode=mode or Mode.SRE)

    def send_status(self, plans: list, mode: Mode = None) -> bool:
        """
        Send status message with pending plans.

        Args:
            plans: List of pending plan dicts
            mode: Optional mode for message prefix
        """
        if not plans:
            return self.send_response("📋 No pending plans.", mode=mode or Mode.SRE)

        lines = ["📋 Pending Plans:\n"]
        for plan in plans[:5]:  # Limit to 5 most recent
            plan_id = plan.get('plan_id', 'unknown')
            severity = plan.get('severity', 'info').upper()
            summary = plan.get('summary', 'Unknown')[:50]
            lines.append(f"• [{severity}] {plan_id}\n  {summary}")

        if len(plans) > 5:
            lines.append(f"\n... and {len(plans) - 5} more")

        lines.append("\nReply: approve <id> or reject <id>")

        return self.send_response("\n".join(lines), mode=mode or Mode.SRE)
