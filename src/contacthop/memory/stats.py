"""Per-contact, per-channel responsiveness: how fast does this human actually
reply on each channel? Feeds the policy engine's urgency decisions."""

from __future__ import annotations

import uuid
from datetime import datetime
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contacthop.domain.enums import ChannelType, Direction
from contacthop.domain.models import Conversation, Message


def reply_latencies(
    messages: list[tuple[ChannelType, Direction, datetime]],
) -> dict[ChannelType, float]:
    """Median seconds from an outbound message to the next inbound, per channel.

    ``messages`` must be ordered oldest-first. Consecutive outbounds keep the
    earliest unanswered timestamp — the human's latency is measured from when
    we first asked, not from our last nudge.
    """
    awaiting: dict[ChannelType, datetime] = {}
    latencies: dict[ChannelType, list[float]] = {}
    for channel, direction, created_at in messages:
        if direction == Direction.OUTBOUND:
            awaiting.setdefault(channel, created_at)
        else:
            started = awaiting.pop(channel, None)
            if started is not None:
                latencies.setdefault(channel, []).append(
                    (created_at - started).total_seconds()
                )
    return {channel: median(values) for channel, values in latencies.items()}


async def channel_responsiveness(
    session: AsyncSession, contact_id: uuid.UUID
) -> dict[ChannelType, float]:
    result = await session.execute(
        select(Message.channel, Message.direction, Message.created_at)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.contact_id == contact_id)
        .order_by(Message.created_at, Message.id)
    )
    return reply_latencies([tuple(row) for row in result.all()])
