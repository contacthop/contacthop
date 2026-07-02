from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from contacthop.api.deps import AdaptersDep, SessionDep
from contacthop.domain.enums import EventType
from contacthop.domain.models import Contact, Conversation, ConversationEvent, Message
from contacthop.domain.schemas import (
    AgentMessageCreate,
    ChannelSwitchRequest,
    ConversationCreate,
    ConversationRead,
    EventRead,
    MessageRead,
)
from contacthop.outbound.gateway import send_agent_message

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


async def _get_conversation(session: AsyncSession, conversation_id: uuid.UUID) -> Conversation:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.contact))
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conversation


@router.post("", response_model=ConversationRead, status_code=201)
async def create_conversation(payload: ConversationCreate, session: SessionDep) -> Conversation:
    contact = await session.get(Contact, payload.contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    conversation = Conversation(
        contact_id=payload.contact_id, goal=payload.goal, current_channel=payload.channel
    )
    session.add(conversation)
    await session.flush()
    return conversation


@router.get("/{conversation_id}", response_model=ConversationRead)
async def get_conversation(conversation_id: uuid.UUID, session: SessionDep) -> Conversation:
    return await _get_conversation(session, conversation_id)


@router.get("/{conversation_id}/transcript", response_model=list[MessageRead])
async def get_transcript(conversation_id: uuid.UUID, session: SessionDep) -> list[Message]:
    await _get_conversation(session, conversation_id)
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return list(result.scalars())


@router.get("/{conversation_id}/events", response_model=list[EventRead])
async def get_events(conversation_id: uuid.UUID, session: SessionDep) -> list[ConversationEvent]:
    await _get_conversation(session, conversation_id)
    result = await session.execute(
        select(ConversationEvent)
        .where(ConversationEvent.conversation_id == conversation_id)
        .order_by(ConversationEvent.created_at, ConversationEvent.id)
    )
    return list(result.scalars())


@router.post("/{conversation_id}/switch", response_model=ConversationRead)
async def switch_channel(
    conversation_id: uuid.UUID, payload: ChannelSwitchRequest, session: SessionDep
) -> Conversation:
    """Explicitly move the conversation to another channel; subsequent sends default there."""
    conversation = await _get_conversation(session, conversation_id)
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
) -> Message:
    conversation = await _get_conversation(session, conversation_id)
    return await send_agent_message(session, conversation, payload, adapters)
