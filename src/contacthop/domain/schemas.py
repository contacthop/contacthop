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
    SessionState,
    Urgency,
    WebhookDeliveryStatus,
)


class ChannelIdentityCreate(BaseModel):
    channel: ChannelType
    address: str = Field(min_length=3, max_length=320)


class ChannelIdentityRead(ChannelIdentityCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    verified: bool
    opted_out: bool


class ContactCreate(BaseModel):
    display_name: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)
    identities: list[ChannelIdentityCreate] = Field(default_factory=list)


class ContactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID | None = None
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
    agent_id: uuid.UUID | None = None
    status: ConversationStatus
    current_channel: ChannelType
    goal: str | None
    created_at: datetime


class AgentMessageCreate(BaseModel):
    """An agent reply. Channel is optional — omit it and the policy engine decides."""

    body: str = Field(min_length=1)
    channel: ChannelType | None = None
    urgency: Urgency = Urgency.NORMAL
    # Seconds to wait for a human reply before firing a no-reply escalation event.
    follow_up_after: float | None = Field(default=None, ge=0)


class ChannelSwitchRequest(BaseModel):
    channel: ChannelType
    reason: str = "agent requested"


class CallRequest(BaseModel):
    """Originate a voice call; ``body`` is the agent's opening line once answered."""

    body: str | None = None


class ChannelSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    channel: ChannelType
    state: SessionState
    session_meta: dict[str, Any]
    created_at: datetime
    closed_at: datetime | None


class EmailInboundPayload(BaseModel):
    """Normalized inbound email. Bridge any provider's inbound-parse webhook to this."""

    from_address: str
    to_address: str = ""
    subject: str | None = None
    text: str = Field(min_length=1)
    message_id: str | None = None
    in_reply_to: str | None = None


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


class MemoryFactCreate(BaseModel):
    """Something the agent decided to remember about a contact."""

    text: str = Field(min_length=1, max_length=2000)
    topic: str | None = Field(default=None, max_length=100)
    conversation_id: uuid.UUID | None = None


class MemoryFact(MemoryFactCreate):
    id: uuid.UUID
    created_at: datetime


class ContactMemoryFact(MemoryFact):
    """A fact with its owner — returned by cross-contact topic queries."""

    contact_id: uuid.UUID


class ConversationContextRead(BaseModel):
    """What an agent needs to compose its next turn: goal, digest, recent verbatim."""

    conversation_id: uuid.UUID
    goal: str | None
    current_channel: ChannelType
    summary: str
    recent_messages: list[MessageRead]
    # Durable facts about the contact from the memory store (empty when disabled).
    memory: list[MemoryFact] = Field(default_factory=list)


class ContactStatsRead(BaseModel):
    contact_id: uuid.UUID
    # Median seconds from an agent message to the human's reply, per channel.
    median_reply_seconds: dict[ChannelType, float]


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    webhook_url: str | None = Field(default=None, max_length=500)


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    webhook_url: str | None
    created_at: datetime


class AgentCreatedRead(AgentRead):
    """Returned once, at creation or key rotation — the key is never shown again."""

    api_key: str


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    webhook_url: str | None = Field(default=None, max_length=500)


class AgentDeliveryRead(BaseModel):
    """A webhook notification's delivery state (the outbox / dead letter view)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID | None = None
    event: str
    conversation_id: uuid.UUID | None
    payload: dict[str, Any]
    status: WebhookDeliveryStatus
    attempts: int
    next_attempt_at: datetime
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None


class AgentNotification(BaseModel):
    """Payload pushed to the agent's webhook when a conversation event occurs."""

    event: str
    conversation_id: uuid.UUID
    contact_id: uuid.UUID
    message: MessageRead | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
