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

        # User mode state per sender (defaults to SRE)
        self.user_mode = {}  # {sender_number: Mode}

    def get_mode(self, sender: str) -> Mode:
        """Get current mode for a sender (defaults to SRE)."""
        return self.user_mode.get(sender, Mode.SRE)

    def set_mode(self, sender: str, mode: Mode):
        """Set mode for a sender."""
        self.user_mode[sender] = mode

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

            # Parse the command (pass sender for operator mode state)
            cmd = self._parse_command(text, sender=sender)
            if cmd:
                cmd['sender'] = sender
                cmd['timestamp'] = timestamp
                cmd['raw_text'] = text

            return cmd

        except Exception as e:
            logger.error(f"Failed to parse message: {e}")
            return None

    def _parse_command(self, text: str, sender: str = None) -> Optional[dict]:
        """
        Parse command text into structured command.

        Mode switch commands (work in any mode):
        - /sre - Switch to SRE mode (default, includes HA control)
        - /operator - Switch to operator mode

        Args:
            text: Message text
            sender: Sender phone number (for mode state)

        Returns:
            Command dict with 'action' and optional params
        """
        text_lower = text.lower().strip()
        text_orig = text.strip()  # Keep original case for content

        # Mode switch commands (work from any mode)
        if text_lower == '/sre':
            return {'action': 'mode_switch', 'mode': Mode.SRE, 'sender': sender}
        if text_lower == '/operator':
            return {'action': 'mode_switch', 'mode': Mode.OPERATOR, 'sender': sender}

        # Route based on current mode
        current_mode = self.get_mode(sender)

        if current_mode == Mode.OPERATOR:
            return self._parse_operator_command(text_orig, sender)
        else:  # SRE mode (default)
            return self._parse_sre_command(text_lower, sender)

    def _parse_sre_command(self, text: str, sender: str) -> Optional[dict]:
        """
        Parse SRE mode commands.

        SRE commands:
        - approve <plan_id> / yes <plan_id> / ok <plan_id>
        - reject <plan_id> / no <plan_id> / deny <plan_id>
        - status / ?
        - help

        Args:
            text: Lowercase message text
            sender: Sender phone number

        Returns:
            Command dict with 'action' and optional 'plan_id'
        """
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
        if text in ['status', 'pending', 'list', 'plans', '?']:
            return {'action': 'status'}

        # Help command
        if text in ['help', 'commands']:
            return {'action': 'help'}

        # Any other message - treat as chat for Claude
        logger.debug(f"Routing to chat: {text}")
        return {'action': 'chat', 'text': text}

    def _parse_operator_command(self, text: str, sender: str) -> Optional[dict]:
        """
        Parse operator mode commands.

        Operator commands:
        - memory show - Display memory.md
        - memory add <text> - Add to memory
        - memory clear - Clear memory
        - rules list - List rule files
        - rules show <name> - Show a rule
        - rules add <name> <content> - Add to a rule
        - context - Show loaded context files
        - reload - Reload context
        - exit - Exit operator mode

        Args:
            text: Command text
            sender: Sender phone number

        Returns:
            Command dict with 'action' and params
        """
        text = text.strip()
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower() if parts else ''

        # Exit operator mode (switch back to SRE)
        if cmd in ['exit', 'quit', 'done', 'back']:
            return {'action': 'mode_switch', 'mode': Mode.SRE, 'sender': sender}

        # Memory commands
        if cmd == 'memory':
            subcmd = parts[1].lower() if len(parts) > 1 else 'show'

            if subcmd == 'show':
                return {'action': 'operator_memory_show'}
            elif subcmd == 'add' and len(parts) > 2:
                return {'action': 'operator_memory_add', 'text': parts[2]}
            elif subcmd == 'clear':
                return {'action': 'operator_memory_clear'}
            else:
                return {'action': 'operator_help', 'topic': 'memory'}

        # Rules commands
        if cmd == 'rules':
            subcmd = parts[1].lower() if len(parts) > 1 else 'list'

            if subcmd == 'list':
                return {'action': 'operator_rules_list'}
            elif subcmd == 'show' and len(parts) > 2:
                return {'action': 'operator_rules_show', 'name': parts[2]}
            elif subcmd == 'add' and len(parts) > 2:
                # Format: rules add <name> <content>
                # Content can have spaces, so split differently
                rest = text[len('rules add '):].strip()
                name_parts = rest.split(maxsplit=1)
                if len(name_parts) == 2:
                    return {'action': 'operator_rules_add', 'name': name_parts[0], 'content': name_parts[1]}
            return {'action': 'operator_help', 'topic': 'rules'}

        # Context command
        if cmd == 'context':
            return {'action': 'operator_context'}

        # Reload command
        if cmd == 'reload':
            return {'action': 'operator_reload'}

        # Help command (in operator mode)
        if cmd in ['help', '?']:
            return {'action': 'operator_help'}

        # Natural language - route to Claude SDK
        return {'action': 'operator_chat', 'text': text}

    def send_response(self, message: str, mode: Mode = None) -> bool:
        """
        Send a response message via Signal with optional mode prefix.

        Args:
            message: Response text to send
            mode: Optional mode to prefix message with (e.g., [SRE], [OPERATOR])

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

• approve [plan_id] - Execute a pending plan
• reject [plan_id] - Dismiss a pending plan
• status - List all pending plans
• help - Show this message

Reactions: 👍 approve, 👎 reject, 🔍 reinvestigate

Modes: /sre (default), /operator"""

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
