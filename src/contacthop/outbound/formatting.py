"""Channel-specific formatting hints for outbound sends."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contacthop.domain.enums import ChannelType
from contacthop.domain.models import Conversation, Message


def _reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


async def email_send_meta(
    session: AsyncSession, conversation: Conversation
) -> dict[str, Any]:
    """Subject + RFC 5322 threading headers so the human's mail client keeps one thread."""
    result = await session.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.channel == ChannelType.EMAIL,
        )
        .order_by(Message.created_at.desc(), Message.id)
        .limit(1)
    )
    last_email = result.scalar_one_or_none()

    if last_email is None:
        return {
            "subject": conversation.goal or "Message from your assistant",
            "in_reply_to": None,
            "references": [],
        }

    last_meta = last_email.channel_meta or {}
    in_reply_to = last_meta.get("provider_message_id")
    references = [r for r in last_meta.get("references", []) if r]
    if in_reply_to and in_reply_to not in references:
        references.append(in_reply_to)
    subject = last_meta.get("subject") or conversation.goal or "Message from your assistant"
    return {
        "subject": _reply_subject(subject),
        "in_reply_to": in_reply_to,
        "references": references,
    }
