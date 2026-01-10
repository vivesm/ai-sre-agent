"""Claude Agent SDK wrapper for Signal bot with session persistence."""
import asyncio
import logging
from typing import Tuple, Optional
from claude_agent_sdk import query, ClaudeAgentOptions

logger = logging.getLogger('ai-sre-agent.sdk')

CLAUDE_CLI = '/home/melvin/.nvm/versions/node/v20.19.6/bin/claude'
WORKING_DIR = '/home/melvin/server'


async def query_claude(
    message: str,
    system_prompt: str = None,
    session_id: str = None
) -> Tuple[str, Optional[str]]:
    """Send a query to Claude with full tool access and session support.

    Args:
        message: User message to send
        system_prompt: Optional system prompt for context
        session_id: Optional session ID to resume conversation

    Returns:
        Tuple of (response text, session_id for next call)
    """
    # Build prompt - only include system_prompt for NEW sessions
    if session_id:
        # Resuming: just send the message, session has context
        full_prompt = message
    else:
        # New session: include system prompt
        full_prompt = message
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n---\n\nUser: {message}"

    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Write", "Bash", "Glob", "Grep"],
        cwd=WORKING_DIR,
        cli_path=CLAUDE_CLI,
        permission_mode='bypassPermissions',
        resume=session_id,
    )

    last_text = None
    new_session_id = None
    try:
        async for msg in query(prompt=full_prompt, options=options):
            # Only keep the LAST text response (avoid duplicates)
            if hasattr(msg, 'content'):
                for block in msg.content:
                    if hasattr(block, 'text'):
                        last_text = block.text
            # Capture session ID from ResultMessage
            if hasattr(msg, 'session_id'):
                new_session_id = msg.session_id
            if hasattr(msg, 'result') and msg.result:
                last_text = str(msg.result)
    except Exception as e:
        logger.error(f"Claude SDK error: {e}")
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
