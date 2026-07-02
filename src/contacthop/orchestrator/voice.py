"""Voice session state and the queued-speech delivery model.

While a call is open, agent replies on the voice channel become QUEUED
messages; the call's TwiML loop drains and speaks them, marking them SENT.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contacthop.domain.enums import (
    ChannelType,
    DeliveryStatus,
    Direction,
    SessionState,
)
from contacthop.domain.models import ChannelSession, Conversation, Message, utcnow


async def get_open_session(
    session: AsyncSession, conversation_id: uuid.UUID
) -> ChannelSession | None:
    result = await session.execute(
        select(ChannelSession).where(
            ChannelSession.conversation_id == conversation_id,
            ChannelSession.channel == ChannelType.VOICE,
            ChannelSession.state == SessionState.OPEN,
        )
    )
    return result.scalars().first()


async def open_session(
    session: AsyncSession, conversation_id: uuid.UUID, call_sid: str, adapter_name: str
) -> ChannelSession:
    channel_session = ChannelSession(
        conversation_id=conversation_id,
        channel=ChannelType.VOICE,
        session_meta={"call_sid": call_sid, "adapter": adapter_name},
    )
    session.add(channel_session)
    await session.flush()
    return channel_session


async def close_open_session(
    session: AsyncSession, conversation_id: uuid.UUID, reason: str
) -> ChannelSession | None:
    channel_session = await get_open_session(session, conversation_id)
    if channel_session is not None:
        channel_session.state = SessionState.CLOSED
        channel_session.closed_at = utcnow()
        channel_session.session_meta = {**channel_session.session_meta, "close_reason": reason}
        await session.flush()
    return channel_session


async def queue_speech(
    session: AsyncSession, conversation: Conversation, body: str, reason: str
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        direction=Direction.OUTBOUND,
        channel=ChannelType.VOICE,
        body=body,
        channel_meta={"policy_reason": reason},
        delivery_status=DeliveryStatus.QUEUED,
    )
    session.add(message)
    await session.flush()
    return message


async def drain_queued_speech(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[Message]:
    """Pop all queued voice messages for the call to speak, marking them SENT."""
    result = await session.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.channel == ChannelType.VOICE,
            Message.direction == Direction.OUTBOUND,
            Message.delivery_status == DeliveryStatus.QUEUED,
        )
        .order_by(Message.created_at, Message.id)
    )
    messages = list(result.scalars())
    for message in messages:
        message.delivery_status = DeliveryStatus.SENT
    if messages:
        await session.flush()
    return messages
