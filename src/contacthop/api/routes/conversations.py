from __future__ import annotations

import uuid
from typing import cast

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from contacthop.api.deps import (
    AdaptersDep,
    MemoryDep,
    Principal,
    PrincipalDep,
    SessionDep,
    SettingsDep,
    ensure_visible,
)
from contacthop.channels.base import ChannelSendError, VoiceAdapter
from contacthop.domain.enums import ChannelType, ConversationStatus, EventType
from contacthop.domain.models import (
    ChannelSession,
    Contact,
    Conversation,
    ConversationEvent,
    Message,
)
from contacthop.domain.schemas import (
    AgentMessageCreate,
    CallRequest,
    ChannelSessionRead,
    ChannelSwitchRequest,
    ConversationContextRead,
    ConversationCreate,
    ConversationRead,
    EventRead,
    MessageRead,
)
from contacthop.memory.transcript import build_context
from contacthop.orchestrator.voice import get_open_session, open_session, queue_speech
from contacthop.orchestrator.windows import channel_window, open_channels
from contacthop.outbound.gateway import send_agent_message
from contacthop.outbound.limits import enforce_rate_limit

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


async def _get_conversation(
    session: AsyncSession, conversation_id: uuid.UUID, principal: Principal
) -> Conversation:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.contact))
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    ensure_visible(conversation.agent_id, principal)
    return conversation


@router.get("", response_model=list[ConversationRead])
async def list_conversations(
    session: SessionDep,
    principal: PrincipalDep,
    status: ConversationStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Conversation]:
    query = select(Conversation).order_by(Conversation.created_at.desc(), Conversation.id)
    if principal.agent is not None:
        query = query.where(Conversation.agent_id == principal.agent.id)
    if status is not None:
        query = query.where(Conversation.status == status)
    result = await session.execute(query.limit(limit).offset(offset))
    return list(result.scalars())


@router.post("", response_model=ConversationRead, status_code=201)
async def create_conversation(
    payload: ConversationCreate, session: SessionDep, principal: PrincipalDep
) -> Conversation:
    contact = await session.get(Contact, payload.contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    ensure_visible(contact.agent_id, principal)
    conversation = Conversation(
        contact_id=payload.contact_id,
        goal=payload.goal,
        current_channel=payload.channel,
        agent_id=contact.agent_id,
    )
    session.add(conversation)
    await session.flush()
    return conversation


@router.get("/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Conversation:
    return await _get_conversation(session, conversation_id, principal)


@router.get("/{conversation_id}/transcript", response_model=list[MessageRead])
async def get_transcript(
    conversation_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> list[Message]:
    await _get_conversation(session, conversation_id, principal)
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return list(result.scalars())


@router.get("/{conversation_id}/context", response_model=ConversationContextRead)
async def get_context(
    conversation_id: uuid.UUID,
    session: SessionDep,
    memory: MemoryDep,
    principal: PrincipalDep,
    recent: int = 20,
) -> ConversationContextRead:
    """Prompt-ready context: digest of older history, the recent window verbatim,
    and remembered facts about the contact."""
    conversation = await _get_conversation(session, conversation_id, principal)
    summary, recent_messages = await build_context(session, conversation, recent_window=recent)
    return ConversationContextRead(
        conversation_id=conversation.id,
        goal=conversation.goal,
        current_channel=conversation.current_channel,
        summary=summary,
        recent_messages=[MessageRead.model_validate(m) for m in recent_messages],
        memory=await memory.recall(conversation.contact_id, limit=25),
    )


@router.get("/{conversation_id}/events", response_model=list[EventRead])
async def get_events(
    conversation_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> list[ConversationEvent]:
    await _get_conversation(session, conversation_id, principal)
    result = await session.execute(
        select(ConversationEvent)
        .where(ConversationEvent.conversation_id == conversation_id)
        .order_by(ConversationEvent.created_at, ConversationEvent.id)
    )
    return list(result.scalars())


@router.post("/{conversation_id}/switch", response_model=ConversationRead)
async def switch_channel(
    conversation_id: uuid.UUID,
    payload: ChannelSwitchRequest,
    session: SessionDep,
    principal: PrincipalDep,
) -> Conversation:
    """Explicitly move the conversation to another channel; subsequent sends default there."""
    conversation = await _get_conversation(session, conversation_id, principal)
    if conversation.current_channel != payload.channel:
        session.add(
            ConversationEvent(
                conversation_id=conversation.id,
                type=EventType.CHANNEL_SWITCH,
                payload={
                    "from": conversation.current_channel,
                    "to": payload.channel,
                    "reason": payload.reason,
                },
            )
        )
        conversation.current_channel = payload.channel
        await session.flush()
    return conversation


@router.post("/{conversation_id}/messages", response_model=MessageRead, status_code=201)
async def send_message(
    conversation_id: uuid.UUID,
    payload: AgentMessageCreate,
    session: SessionDep,
    adapters: AdaptersDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> Message:
    conversation = await _get_conversation(session, conversation_id, principal)
    return await send_agent_message(session, conversation, payload, adapters, settings)


@router.post("/{conversation_id}/call", response_model=ChannelSessionRead, status_code=201)
async def originate_call(
    conversation_id: uuid.UUID,
    payload: CallRequest,
    request: Request,
    session: SessionDep,
    adapters: AdaptersDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> ChannelSession:
    """Dial the contact now. ``body`` becomes the agent's opening line once answered."""
    conversation = await _get_conversation(session, conversation_id, principal)
    await enforce_rate_limit(session, settings, conversation.contact)

    adapter = adapters.get(ChannelType.VOICE)
    if adapter is None or not hasattr(adapter, "originate_call"):
        raise HTTPException(status_code=422, detail="no voice adapter configured")
    voice_adapter = cast(VoiceAdapter, adapter)
    if ChannelType.VOICE not in open_channels(
        settings, conversation.contact, {ChannelType.VOICE}
    ):
        window = channel_window(settings, conversation.contact, ChannelType.VOICE)
        raise HTTPException(
            status_code=422,
            detail=f"voice is outside its send window ({window}); cannot place a call now",
        )
    if await get_open_session(session, conversation.id) is not None:
        raise HTTPException(status_code=409, detail="a call is already in progress")

    # Voice dials a phone number: a dedicated voice identity, or the SMS number.
    identities = {i.channel: i for i in conversation.contact.identities}
    identity = identities.get(ChannelType.VOICE) or identities.get(ChannelType.SMS)
    if identity is None:
        raise HTTPException(status_code=422, detail="contact has no phone number identity")
    if identity.opted_out:
        raise HTTPException(
            status_code=403,
            detail="contact has opted out of this number; calling is not permitted",
        )

    base = settings.public_base_url or str(request.base_url).rstrip("/")
    answer_url = f"{base}/webhooks/twilio/voice/answer?conversation_id={conversation.id}"
    status_url = f"{base}/webhooks/twilio/voice/status?conversation_id={conversation.id}"
    try:
        receipt = await voice_adapter.originate_call(identity.address, answer_url, status_url)
    except ChannelSendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    channel_session = await open_session(
        session, conversation.id, receipt.provider_message_id, str(receipt.meta.get("adapter"))
    )
    if payload.body:
        await queue_speech(session, conversation, payload.body, "call opening line")

    if conversation.current_channel != ChannelType.VOICE:
        session.add(
            ConversationEvent(
                conversation_id=conversation.id,
                type=EventType.CHANNEL_SWITCH,
                payload={
                    "from": conversation.current_channel,
                    "to": ChannelType.VOICE,
                    "reason": "call originated",
                },
            )
        )
        conversation.current_channel = ChannelType.VOICE
        await session.flush()
    return channel_session


@router.get("/{conversation_id}/sessions", response_model=list[ChannelSessionRead])
async def list_sessions(
    conversation_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> list[ChannelSession]:
    await _get_conversation(session, conversation_id, principal)
    result = await session.execute(
        select(ChannelSession)
        .where(ChannelSession.conversation_id == conversation_id)
        .order_by(ChannelSession.created_at, ChannelSession.id)
    )
    return list(result.scalars())
