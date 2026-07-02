"""Conversation context for agent prompts: a compact digest of the past plus
recent messages verbatim, so long conversations fit an LLM context window.

The digest is deterministic (no LLM dependency — the harness stays
agent-agnostic). Swap in an LLM-backed summarizer by replacing ``digest()``
at the call site; the endpoint contract doesn't change.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contacthop.domain.enums import Direction
from contacthop.domain.models import Conversation, Message

# Messages returned verbatim; everything older is digested.
RECENT_WINDOW = 20
# Hard cap on digest lines so context stays bounded no matter the history.
DIGEST_MAX_LINES = 40
DIGEST_SNIPPET_CHARS = 90


def digest(messages: list[Message]) -> str:
    """One line per message, oldest first, capped at DIGEST_MAX_LINES (keeps the tail)."""
    if not messages:
        return ""
    omitted = max(0, len(messages) - DIGEST_MAX_LINES)
    lines = []
    if omitted:
        lines.append(f"({omitted} earlier message(s) omitted)")
    for message in messages[omitted:]:
        speaker = "them" if message.direction is Direction.INBOUND else "agent"
        body = " ".join(message.body.split())
        if len(body) > DIGEST_SNIPPET_CHARS:
            body = body[: DIGEST_SNIPPET_CHARS - 1] + "…"
        lines.append(f"[{message.channel}] {speaker}: {body}")
    return "\n".join(lines)


async def build_context(
    session: AsyncSession, conversation: Conversation, recent_window: int = RECENT_WINDOW
) -> tuple[str, list[Message]]:
    """Return (summary_digest, recent_messages) for the conversation."""
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at, Message.id)
    )
    messages = list(result.scalars())
    recent_window = max(1, recent_window)
    return digest(messages[:-recent_window]), messages[-recent_window:]
