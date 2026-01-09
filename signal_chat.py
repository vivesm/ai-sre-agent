#!/usr/bin/env python3
"""Signal chatbot using signalbot library with Claude integration."""

import os
import subprocess
import logging
import json
from pathlib import Path
from dotenv import load_dotenv
from signalbot import SignalBot, Command, Context

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('signal-chat')

# Configuration from environment
SIGNAL_PHONE = os.getenv('SIGNAL_PHONE', '+1234567890')
SIGNAL_SERVICE = os.getenv('SIGNAL_SERVICE', 'localhost:8080')
HA_URL = os.getenv('HA_URL', 'https://homeassistant.local')
HA_TOKEN = os.getenv('HA_TOKEN', '')

# Chat history file
SCRIPT_DIR = Path(__file__).parent
HISTORY_FILE = SCRIPT_DIR / 'data' / 'chat_history.json'

# Multi-server SSH access
SERVERS = {
    "atom": "localhost",
    "blackest": os.getenv('SSH_BLACKEST', 'blackest.local'),
    "sagan": os.getenv('SSH_SAGAN', 'sagan.local'),
}

# CLAUDE.md context files to load (in priority order)
CONTEXT_FILES = [
    Path.home() / 'CLAUDE.md',
    Path.home() / 'server' / 'CLAUDE.md',
    Path.home() / '.claude' / 'CLAUDE.md',
]

# Simple queries that don't need full Claude (use cache or quick response)
SIMPLE_PATTERNS = ['status', 'temp', 'cpu', 'memory', 'disk', 'ip', 'uptime', 'containers']

# Cache for system context (refresh every 30 seconds)
_context_cache = {'data': None, 'time': 0}


def load_claude_context() -> str:
    """Load CLAUDE.md files for infrastructure context."""
    context_parts = []
    for path in CONTEXT_FILES:
        if path.exists():
            try:
                content = path.read_text()
                # Extract key sections (first 1500 chars to keep context manageable)
                # Focus on emergency, commands, and architecture sections
                lines = content.split('\n')
                key_lines = []
                in_key_section = False
                for line in lines:
                    # Capture headers and key info
                    if line.startswith('#') or 'Emergency' in line or 'Command' in line:
                        in_key_section = True
                    if in_key_section:
                        key_lines.append(line)
                    if len('\n'.join(key_lines)) > 800:
                        break
                if key_lines:
                    context_parts.append(f"[{path.name}]\n" + '\n'.join(key_lines[:30]))
            except Exception as e:
                logger.warning(f"Failed to read {path}: {e}")
    return '\n\n'.join(context_parts) if context_parts else ""


def is_simple_query(text: str) -> bool:
    """Check if query can be answered from cached system data."""
    text_lower = text.lower()
    return any(p in text_lower for p in SIMPLE_PATTERNS) and len(text) < 50


def get_quick_response(text: str, context: str) -> str:
    """Generate quick response for simple queries without calling Claude."""
    text_lower = text.lower()
    lines = context.split('\n')

    if 'temp' in text_lower or 'cpu' in text_lower:
        for line in lines:
            if 'CPU Temp' in line:
                temp_c = line.split(':')[1].strip()
                # Convert to F if requested
                if 'f' in text_lower or 'fahrenheit' in text_lower:
                    c = float(temp_c.replace('°C', ''))
                    return f"CPU: {c * 9/5 + 32:.0f}°F"
                return f"CPU: {temp_c}"

    if 'ip' in text_lower:
        for line in lines:
            if 'Public IP' in line:
                return line.split(':')[1].strip()

    if 'uptime' in text_lower:
        for line in lines:
            if 'Uptime' in line:
                return line.split(':', 1)[1].strip()

    if 'memory' in text_lower or 'ram' in text_lower:
        for line in lines:
            if 'Memory' in line:
                return line.split(':', 1)[1].strip()

    if 'disk' in text_lower or 'space' in text_lower:
        for line in lines:
            if 'Disk' in line:
                return line.split(':', 1)[1].strip()

    if 'container' in text_lower:
        for line in lines:
            if 'Containers' in line:
                return line.split(':', 1)[1].strip()

    return None  # Fall through to Claude


def load_chat_history() -> list:
    """Load recent chat messages for context."""
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE) as f:
                history = json.load(f)
            return history[-10:]  # Last 5 exchanges
        except:
            pass
    return []


def save_chat_history(user_msg: str, assistant_msg: str):
    """Save chat exchange to history."""
    history = load_chat_history()
    history.append({'role': 'user', 'content': user_msg})
    history.append({'role': 'assistant', 'content': assistant_msg[:500]})  # Truncate long responses
    # Keep last 20 messages
    history = history[-20:]
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f)


def format_chat_history(history: list) -> str:
    """Format chat history for prompt."""
    if not history:
        return "(No recent messages)"
    lines = []
    for msg in history[-6:]:  # Last 3 exchanges
        role = "User" if msg['role'] == 'user' else "You"
        lines.append(f"{role}: {msg['content'][:150]}")
    return '\n'.join(lines)


def get_system_context() -> str:
    """Gather real system data for Claude context."""
    context_parts = []

    # CPU Temperature
    try:
        temp_path = Path('/sys/class/thermal/thermal_zone0/temp')
        if temp_path.exists():
            temp_raw = temp_path.read_text().strip()
            temp_c = int(temp_raw) / 1000
            context_parts.append(f"CPU Temp: {temp_c:.1f}°C")
    except Exception as e:
        logger.warning(f"Failed to read CPU temp: {e}")

    # Memory
    try:
        meminfo = Path('/proc/meminfo').read_text()
        mem_total = mem_avail = None
        for line in meminfo.split('\n'):
            if line.startswith('MemTotal:'):
                mem_total = int(line.split()[1]) // 1024  # MB
            elif line.startswith('MemAvailable:'):
                mem_avail = int(line.split()[1]) // 1024  # MB
        if mem_total and mem_avail:
            mem_used = mem_total - mem_avail
            mem_pct = (mem_used / mem_total) * 100
            context_parts.append(f"Memory: {mem_used}MB/{mem_total}MB ({mem_pct:.0f}% used)")
    except Exception as e:
        logger.warning(f"Failed to read memory: {e}")

    # Disk Usage
    try:
        result = subprocess.run(['df', '-h', '/', '/docker'],
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')[1:]  # Skip header
            disk_info = []
            for line in lines:
                parts = line.split()
                if len(parts) >= 5:
                    disk_info.append(f"{parts[5]}: {parts[4]} used ({parts[2]}/{parts[1]})")
            if disk_info:
                context_parts.append(f"Disk: {', '.join(disk_info)}")
    except Exception as e:
        logger.warning(f"Failed to get disk usage: {e}")

    # Docker Containers
    try:
        result = subprocess.run(
            ['docker', 'ps', '--format', '{{.Names}}: {{.Status}}'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            containers = result.stdout.strip().split('\n')
            healthy = [c for c in containers if 'healthy' in c.lower() and 'unhealthy' not in c.lower()]
            unhealthy = [c.split(':')[0] for c in containers if 'unhealthy' in c.lower()]
            context_parts.append(f"Containers: {len(containers)} running, {len(unhealthy)} unhealthy")
            if unhealthy:
                context_parts.append(f"Unhealthy: {', '.join(unhealthy)}")
    except Exception as e:
        logger.warning(f"Failed to get docker status: {e}")

    # Load Average
    try:
        loadavg = Path('/proc/loadavg').read_text().strip()
        load_1, load_5, load_15 = loadavg.split()[:3]
        context_parts.append(f"Load: {load_1} (1m), {load_5} (5m), {load_15} (15m)")
    except Exception as e:
        logger.warning(f"Failed to read load average: {e}")

    # Public IP
    try:
        result = subprocess.run(['curl', '-s', '--max-time', '3', 'ifconfig.me'],
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            context_parts.append(f"Public IP: {result.stdout.strip()}")
    except Exception as e:
        logger.warning(f"Failed to get public IP: {e}")

    # Uptime
    try:
        result = subprocess.run(['uptime', '-p'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            context_parts.append(f"Uptime: {result.stdout.strip()}")
    except Exception as e:
        logger.warning(f"Failed to get uptime: {e}")

    # Home Assistant API info
    context_parts.append(f"\nHome Assistant: {HA_URL}")
    context_parts.append(f"HA Token: {HA_TOKEN}")
    context_parts.append("To control HA devices, use curl like:")
    context_parts.append(f'  curl -sX POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d \'{{"entity_id":"light.melvins_lamp"}}\' {HA_URL}/api/services/light/turn_on')

    # Multi-server SSH access
    context_parts.append("\nSSH Access to other servers:")
    context_parts.append("  ssh blackest.local  # Mac Mini - has iPhoto library, media files")
    context_parts.append("  ssh sagan.local     # Signal API server, backups")
    context_parts.append("Example: ssh blackest.local 'ls ~/Desktop/iPhoto*'")

    return '\n'.join(context_parts) if context_parts else "System data unavailable"


class ChatCommand(Command):
    """Handle all incoming messages with Claude."""

    async def handle(self, c: Context) -> None:
        text = c.message.text
        if not text:
            return

        logger.info(f"Received: {text}")

        # Gather system context
        system_context = get_system_context()

        # Try quick response for simple queries (no Claude call needed)
        if is_simple_query(text):
            quick = get_quick_response(text, system_context)
            if quick:
                await c.send(quick)
                save_chat_history(text, quick)
                logger.info(f"Quick response: {quick}")
                return

        # For complex queries, send "thinking" message
        await c.send("...")

        # Load chat history and infrastructure context
        chat_history = load_chat_history()
        history_text = format_chat_history(chat_history)
        infra_context = load_claude_context()
        logger.info(f"System context:\n{system_context}")

        # Build prompt with real system data, infrastructure docs, and conversation history
        prompt = f"""You are an SRE assistant responding via Signal to a home server admin.
Keep responses SHORT (under 500 chars) - this is mobile messaging.

## Infrastructure Knowledge
{infra_context}

## Current System State
{system_context}

## Recent Conversation
{history_text}

## User Message
{text}

## Instructions
- Answer with actual data from the system state above - it's already been gathered for you
- Be concise and direct - just give the answer
- Use the conversation history to understand context (e.g., if user says "yes", check what you last asked)
- NEVER ask for permission to run commands - just do it
- If info isn't in the system state, run the command to get it
- No markdown formatting (plain text only for Signal)"""

        # Call Claude for response (YOLO mode - 120s timeout for complex ops)
        try:
            result = subprocess.run(
                ['claude', '-p', prompt, '--output-format', 'text', '--dangerously-skip-permissions'],
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode == 0:
                response = result.stdout.strip()[:1500]
                await c.send(response)
                save_chat_history(text, response)
                logger.info(f"Sent: {response[:50]}...")
            else:
                logger.error(f"Claude failed: {result.stderr}")
                await c.send("Sorry, couldn't process that.")
        except subprocess.TimeoutExpired:
            await c.send("Taking too long, try again?")
        except Exception as e:
            logger.error(f"Error: {e}")
            await c.send(f"Error: {e}")


if __name__ == "__main__":
    if not HA_TOKEN:
        logger.warning("HA_TOKEN not set - Home Assistant control disabled")

    bot = SignalBot({
        "signal_service": SIGNAL_SERVICE,
        "phone_number": SIGNAL_PHONE
    })
    bot.register(ChatCommand())
    logger.info(f"Starting Signal chat bot on {SIGNAL_SERVICE}...")
    bot.start()
