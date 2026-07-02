"""Conversation lifecycle: attach inbound messages, resolve identities, log events."""

from __future__ import annotations

import logging
import uuid

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from contacthop.config import Settings
from contacthop.domain.enums import (
    ChannelType,
    ConversationStatus,
    DeliveryStatus,
    Direction,
    EventType,
    FollowUpStatus,
)
from contacthop.domain.models import (
    ChannelIdentity,
    Contact,
    Conversation,
    ConversationEvent,
    FollowUp,
    Message,
)
from contacthop.domain.schemas import AgentNotification, InboundMessage, MessageRead

logger = logging.getLogger("contacthop.orchestrator")


async def resolve_identity(
    session: AsyncSession, channel: ChannelType, address: str
) -> ChannelIdentity:
    """Find the identity for an inbound address, creating a provisional contact if unknown."""
    result = await session.execute(
        select(ChannelIdentity).where(
            ChannelIdentity.channel == channel, ChannelIdentity.address == address
        )
    )
    identity = result.scalar_one_or_none()
    if identity is not None:
        return identity

    contact = Contact(display_name=None, preferences={})
    identity = ChannelIdentity(contact=contact, channel=channel, address=address)
    session.add_all([contact, identity])
    await session.flush()
    logger.info("created provisional contact for unknown %s address %s", channel, address)
    return identity


async def active_conversation_for(
    session: AsyncSession, contact_id: uuid.UUID, channel: ChannelType
) -> Conversation:
    """Most recent active conversation for the contact, or a new one on this channel."""
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.contact_id == contact_id,
            Conversation.status == ConversationStatus.ACTIVE,
        )
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        conversation = Conversation(contact_id=contact_id, current_channel=channel)
        session.add(conversation)
        await session.flush()
    return conversation


async def cancel_follow_ups(session: AsyncSession, conversation_id: uuid.UUID) -> None:
    """The human replied — pending no-reply follow-ups are moot."""
    await session.execute(
        update(FollowUp)
        .where(
            FollowUp.conversation_id == conversation_id,
            FollowUp.status == FollowUpStatus.PENDING,
        )
        .values(status=FollowUpStatus.CANCELLED)
    )


async def record_inbound(session: AsyncSession, inbound: InboundMessage) -> Message:
    """Normalize an adapter-parsed inbound message into the conversation timeline.

    Also cancels any pending no-reply follow-ups — the human replied.
    """
    identity = await resolve_identity(session, inbound.channel, inbound.from_address)
    conversation = await active_conversation_for(session, identity.contact_id, inbound.channel)

    if conversation.current_channel != inbound.channel:
        session.add(
            ConversationEvent(
                conversation_id=conversation.id,
                type=EventType.CHANNEL_SWITCH,
                payload={
                    "from": conversation.current_channel,
                    "to": inbound.channel,
                    "reason": "human replied on a different channel",
                },
            )
        )
        conversation.current_channel = inbound.channel

    await cancel_follow_ups(session, conversation.id)

    message = Message(
        conversation_id=conversation.id,
        direction=Direction.INBOUND,
        channel=inbound.channel,
        body=inbound.body,
        channel_meta={
            "provider_message_id": inbound.provider_message_id,
            **inbound.channel_meta,
        },
        delivery_status=DeliveryStatus.DELIVERED,
    )
    session.add(message)
    await session.flush()
    return message


def inbound_notification(message: Message, contact_id: uuid.UUID) -> AgentNotification:
    return AgentNotification(
        event="conversation.message.received",
        conversation_id=message.conversation_id,
        contact_id=contact_id,
        message=MessageRead.model_validate(message),
    )


async def notify_agent(settings: Settings, notification: AgentNotification) -> None:
    """Push a conversation event to the agent runtime's webhook, if configured."""
    if not settings.agent_webhook_url:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                settings.agent_webhook_url,
                json=notification.model_dump(mode="json"),
            )
    except httpx.HTTPError:
        logger.exception("agent webhook delivery failed")
