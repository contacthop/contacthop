"""Conversation lifecycle: attach inbound messages, resolve identities, log events."""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contacthop.config import Settings
from contacthop.domain.enums import (
    ChannelType,
    ConversationStatus,
    DeliveryStatus,
    Direction,
    EventType,
)
from contacthop.domain.models import (
    ChannelIdentity,
    Contact,
    Conversation,
    ConversationEvent,
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
    session: AsyncSession, contact_id: object, channel: ChannelType
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


async def record_inbound(session: AsyncSession, inbound: InboundMessage) -> Message:
    """Normalize an adapter-parsed inbound message into the conversation timeline."""
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


async def notify_agent(settings: Settings, message: Message, contact_id: object) -> None:
    """Push a conversation event to the agent runtime's webhook, if configured."""
    if not settings.agent_webhook_url:
        return
    notification = AgentNotification(
        event="conversation.message.received",
        conversation_id=message.conversation_id,  # type: ignore[arg-type]
        contact_id=contact_id,  # type: ignore[arg-type]
        message=MessageRead.model_validate(message),
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                settings.agent_webhook_url,
                json=notification.model_dump(mode="json"),
            )
    except httpx.HTTPError:
        logger.exception("agent webhook delivery failed")
