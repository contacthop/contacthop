"""Per-contact rate limiting: the anti-spam half of the gateway backstop.

Counts outbound messages to a contact across all conversations and channels
in the last rolling hour. Enforced below the policy engine, so a buggy or
prompt-injected agent can't flood a human no matter what it asks for.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contacthop.config import Settings
from contacthop.domain.enums import Direction
from contacthop.domain.models import Contact, Conversation, Message, utcnow


async def outbound_last_hour(session: AsyncSession, contact_id: object) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.contact_id == contact_id,
            Message.direction == Direction.OUTBOUND,
            Message.created_at >= utcnow() - timedelta(hours=1),
        )
    )
    return count or 0


async def enforce_rate_limit(
    session: AsyncSession, settings: Settings, contact: Contact
) -> None:
    limit = (contact.preferences or {}).get(
        "max_messages_per_hour", settings.max_messages_per_hour
    )
    if not limit:
        return
    sent = await outbound_last_hour(session, contact.id)
    if sent >= limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"rate limit: {sent} outbound messages to this contact in the "
                f"last hour (limit {limit})"
            ),
        )
