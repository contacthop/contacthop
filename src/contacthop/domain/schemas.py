"""Pydantic v2 schemas shared by the API, webhooks, and agent notifications."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from contacthop.domain.enums import (
    ChannelType,
    ConversationStatus,
    DeliveryStatus,
    Direction,
    EventType,
    Urgency,
)


class ChannelIdentityCreate(BaseModel):
    channel: ChannelType
    address: str = Field(min_length=3, max_length=320)


class ChannelIdentityRead(ChannelIdentityCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    verified: bool


class ContactCreate(BaseModel):
    display_name: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)
    identities: list[ChannelIdentityCreate] = Field(default_factory=list)


class ContactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str | None
    preferences: dict[str, Any]
    identities: list[ChannelIdentityRead]
    created_at: datetime


class ConversationCreate(BaseModel):
    contact_id: uuid.UUID
    goal: str | None = None
    channel: ChannelType = ChannelType.SMS


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    status: ConversationStatus
    current_channel: ChannelType
    goal: str | None
    created_at: datetime


class AgentMessageCreate(BaseModel):
    """An agent reply. Channel is optional — omit it and the policy engine decides."""

    body: str = Field(min_length=1)
    channel: ChannelType | None = None
    urgency: Urgency = Urgency.NORMAL


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    direction: Direction
    channel: ChannelType
    body: str
    channel_meta: dict[str, Any]
    delivery_status: DeliveryStatus
    created_at: datetime


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: EventType
    payload: dict[str, Any]
    created_at: datetime


class InboundMessage(BaseModel):
    """A normalized inbound message, produced by a channel adapter."""

    channel: ChannelType
    from_address: str
    to_address: str
    body: str
    provider_message_id: str | None = None
    channel_meta: dict[str, Any] = Field(default_factory=dict)


class AgentNotification(BaseModel):
    """Payload pushed to the agent's webhook when a conversation event occurs."""

    event: str
    conversation_id: uuid.UUID
    contact_id: uuid.UUID
    message: MessageRead | None = None
