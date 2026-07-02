"""Outbound gateway: every agent reply flows through here.

Runs the policy engine, resolves the contact's address on the chosen channel,
sends via the adapter, persists the message plus any channel-switch event, and
schedules a no-reply follow-up when the agent asks for one.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contacthop.channels.base import ChannelAdapter, ChannelSendError
from contacthop.domain.enums import (
    ChannelType,
    DeliveryStatus,
    Direction,
    EventType,
    FollowUpStatus,
)
from contacthop.domain.models import Conversation, ConversationEvent, FollowUp, Message, utcnow
from contacthop.domain.schemas import AgentMessageCreate
from contacthop.orchestrator.policy import ChannelDecision, PolicyContext, decide
from contacthop.outbound.formatting import email_send_meta


async def send_agent_message(
    session: AsyncSession,
    conversation: Conversation,
    agent_msg: AgentMessageCreate,
    adapters: dict[ChannelType, ChannelAdapter],
) -> Message:
    contact = conversation.contact
    identities = {i.channel: i for i in contact.identities}
    preferred = contact.preferences.get("preferred_channel")

    decision: ChannelDecision = decide(
        PolicyContext(
            current_channel=conversation.current_channel,
            available_channels=set(identities),
            configured_channels=set(adapters),
            body_length=len(agent_msg.body),
            urgency=agent_msg.urgency,
            explicit_channel=agent_msg.channel,
            contact_preferred_channel=ChannelType(preferred) if preferred else None,
        )
    )

    adapter = adapters.get(decision.channel)
    if adapter is None:
        raise HTTPException(
            status_code=422, detail=f"no adapter configured for channel '{decision.channel}'"
        )
    identity = identities.get(decision.channel)
    if identity is None:
        raise HTTPException(
            status_code=422,
            detail=f"contact has no {decision.channel} identity",
        )

    send_meta: dict[str, Any] | None = None
    if decision.channel is ChannelType.EMAIL:
        send_meta = await email_send_meta(session, conversation)

    try:
        receipt = await adapter.send(identity.address, agent_msg.body, send_meta)
    except ChannelSendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if conversation.current_channel != decision.channel:
        session.add(
            ConversationEvent(
                conversation_id=conversation.id,
                type=EventType.CHANNEL_SWITCH,
                payload={
                    "from": conversation.current_channel,
                    "to": decision.channel,
                    "reason": decision.reason,
                },
            )
        )
        conversation.current_channel = decision.channel

    message = Message(
        conversation_id=conversation.id,
        direction=Direction.OUTBOUND,
        channel=decision.channel,
        body=agent_msg.body,
        channel_meta={
            "provider_message_id": receipt.provider_message_id,
            "policy_reason": decision.reason,
            **receipt.meta,
        },
        delivery_status=DeliveryStatus.SENT,
    )
    session.add(message)
    await session.flush()

    if agent_msg.follow_up_after is not None:
        prior_attempts = await session.scalar(
            select(func.count())
            .select_from(FollowUp)
            .where(
                FollowUp.conversation_id == conversation.id,
                FollowUp.status == FollowUpStatus.FIRED,
            )
        )
        session.add(
            FollowUp(
                conversation_id=conversation.id,
                message_id=message.id,
                due_at=utcnow() + timedelta(seconds=agent_msg.follow_up_after),
                attempt=(prior_attempts or 0) + 1,
            )
        )
        await session.flush()

    return message
