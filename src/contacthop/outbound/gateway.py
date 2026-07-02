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
from contacthop.config import Settings
from contacthop.domain.enums import (
    ChannelType,
    DeliveryStatus,
    Direction,
    EventType,
    FollowUpStatus,
)
from contacthop.domain.models import Conversation, ConversationEvent, FollowUp, Message, utcnow
from contacthop.domain.schemas import AgentMessageCreate
from contacthop.memory.stats import channel_responsiveness
from contacthop.orchestrator.policy import ChannelDecision, PolicyContext, decide
from contacthop.orchestrator.voice import get_open_session, queue_speech
from contacthop.orchestrator.windows import channel_window, open_channels
from contacthop.outbound.formatting import email_send_meta
from contacthop.outbound.limits import enforce_rate_limit


async def _maybe_schedule_follow_up(
    session: AsyncSession,
    conversation: Conversation,
    agent_msg: AgentMessageCreate,
    message: Message,
) -> None:
    if agent_msg.follow_up_after is None:
        return
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


async def send_agent_message(
    session: AsyncSession,
    conversation: Conversation,
    agent_msg: AgentMessageCreate,
    adapters: dict[ChannelType, ChannelAdapter],
    settings: Settings,
) -> Message:
    contact = conversation.contact
    identities = {i.channel: i for i in contact.identities}
    preferred = contact.preferences.get("preferred_channel")

    await enforce_rate_limit(session, settings, contact)

    # A live call makes voice available regardless of identities — the open
    # session is the reachable address. Opted-out identities are unreachable.
    open_call = await get_open_session(session, conversation.id)
    available = {ch for ch, ident in identities.items() if not ident.opted_out}
    if open_call is not None:
        available.add(ChannelType.VOICE)

    # Quiet-hours backstop: closed channels are removed from policy input, and
    # a live call exempts voice — the human is already on the line. Explicit
    # and current channels are included so "closed window" is only reported
    # for channels a window actually closes (missing adapters/identities keep
    # their own, more specific errors).
    candidate_channels = set(adapters) | available | {conversation.current_channel}
    if agent_msg.channel is not None:
        candidate_channels.add(agent_msg.channel)
    open_now = open_channels(settings, contact, candidate_channels)
    if open_call is not None:
        open_now.add(ChannelType.VOICE)
    if agent_msg.channel is not None and agent_msg.channel not in open_now:
        window = channel_window(settings, contact, agent_msg.channel)
        raise HTTPException(
            status_code=422,
            detail=(
                f"channel '{agent_msg.channel}' is outside its send window"
                f" ({window}); it reopens later or another channel can be used"
            ),
        )

    decision: ChannelDecision = decide(
        PolicyContext(
            current_channel=conversation.current_channel,
            available_channels=available,
            configured_channels=set(adapters) & open_now,
            body_length=len(agent_msg.body),
            urgency=agent_msg.urgency,
            explicit_channel=agent_msg.channel,
            contact_preferred_channel=ChannelType(preferred) if preferred else None,
            responsiveness=await channel_responsiveness(session, contact.id),
        )
    )
    if decision.channel not in open_now:
        raise HTTPException(
            status_code=422,
            detail="all usable channels are outside their send windows right now",
        )

    adapter = adapters.get(decision.channel)
    if adapter is None:
        raise HTTPException(
            status_code=422, detail=f"no adapter configured for channel '{decision.channel}'"
        )

    if decision.channel is ChannelType.VOICE:
        # Voice is session-based: speech is queued for the live call loop to speak.
        if open_call is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "no open voice session; originate a call first via "
                    "POST /v1/conversations/{id}/call"
                ),
            )
        message = await queue_speech(session, conversation, agent_msg.body, decision.reason)
        await _maybe_schedule_follow_up(session, conversation, agent_msg, message)
        return message

    identity = identities.get(decision.channel)
    if identity is None:
        raise HTTPException(
            status_code=422,
            detail=f"contact has no {decision.channel} identity",
        )
    if identity.opted_out:
        raise HTTPException(
            status_code=403,
            detail=f"contact has opted out of {decision.channel}; sending is not permitted",
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
    await _maybe_schedule_follow_up(session, conversation, agent_msg, message)
    return message
