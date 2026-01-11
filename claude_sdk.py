"""Claude Agent SDK wrapper for Signal bot with session persistence."""
import asyncio
import logging
from typing import Tuple, Optional
from claude_agent_sdk import query, ClaudeAgentOptions

logger = logging.getLogger('ai-sre-agent.sdk')

CLAUDE_CLI = '/home/melvin/.nvm/versions/node/v20.19.6/bin/claude'
WORKING_DIR = '/home/melvin/server'

# Speed hints for adaptive retry on timeout
SPEED_HINT = """TIMEOUT RECOVERY - Use FASTEST methods:
- File sizes: du -sh * | sort -rh | head -10 (not find)
- SSH commands: timeout 30s ssh host 'cmd'
- Avoid: recursive find, -exec, large directory scans
"""


async def query_claude(
    message: str,
    system_prompt: str = None,
    session_id: str = None,
    is_retry: bool = False
) -> Tuple[str, Optional[str]]:
    """Send a query to Claude with full tool access and session support.

    Args:
        message: User message to send
        system_prompt: Optional system prompt for context
        session_id: Optional session ID to resume conversation
        is_retry: Whether this is a retry attempt (prevents infinite loops)

    Returns:
        Tuple of (response text, session_id for next call)
    """
    # Build options with proper system_prompt parameter
    options = ClaudeAgentOptions(
        system_prompt=system_prompt if not session_id else None,  # Only for new sessions
        allowed_tools=["Read", "Write", "Bash", "Glob", "Grep"],
        cwd=WORKING_DIR,
        cli_path=CLAUDE_CLI,
        permission_mode='bypassPermissions',
        resume=session_id,
        max_turns=20,  # Prevent runaway queries
    )

    async def _execute_query(opts: ClaudeAgentOptions) -> Tuple[Optional[str], Optional[str]]:
        """Execute query and return (text, session_id)."""
        text = None
        sid = None
        async for msg in query(prompt=message, options=opts):
            msg_type = type(msg).__name__
            # Only capture text from ResultMessage (final response)
            # Skip intermediate AssistantMessage to avoid duplicates
            if msg_type == 'ResultMessage':
                if hasattr(msg, 'result') and msg.result:
                    text = str(msg.result)
                if hasattr(msg, 'session_id'):
                    sid = msg.session_id
            elif msg_type == 'AssistantMessage':
                # Capture as fallback only if no ResultMessage comes
                if hasattr(msg, 'content'):
                    for block in msg.content:
                        if hasattr(block, 'text'):
                            text = block.text
                if hasattr(msg, 'session_id'):
                    sid = msg.session_id
        return text, sid

    last_text = None
    new_session_id = None
    try:
        last_text, new_session_id = await _execute_query(options)
    except Exception as e:
        error_str = str(e)
        logger.error(f"Claude SDK error: {error_str}")

        # Adaptive retry on timeout - prepend speed hints
        if "exit code 1" in error_str and not is_retry:
            logger.info("Timeout detected - retrying with speed hints...")
            fast_message = f"{SPEED_HINT}\n\nOriginal request: {message}"
            return await query_claude(
                message=fast_message,
                system_prompt=system_prompt,
                session_id=None,  # Fresh session
                is_retry=True  # Prevent infinite loop
            )
        else:
            return f"Error: {e}", None

    response = last_text[:1500] if last_text else "No response from Claude"
    return response, new_session_id


def query_sync(
    message: str,
    system_prompt: str = None,
    session_id: str = None
) -> Tuple[str, Optional[str]]:
    """Synchronous wrapper for query_claude.

    Args:
        message: User message to send
        system_prompt: Optional system prompt for context
        session_id: Optional session ID to resume conversation

    Returns:
        Tuple of (response text, session_id for next call)
    """
    return asyncio.run(query_claude(message, system_prompt, session_id))


# Simple test
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    result = query_sync("What files are in the current directory?")
    print(f"Result: {result}")
