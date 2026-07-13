"""SQLAlchemy 2.0 mapped classes — the channel-agnostic core domain."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypeVar

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, false
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from contacthop.domain.enums import (
    ChannelType,
    ConversationStatus,
    DeliveryStatus,
    Direction,
    EventType,
    FollowUpStatus,
    SessionState,
    WebhookDeliveryStatus,
)

E = TypeVar("E", bound=StrEnum)


def str_enum(enum_cls: type[E], length: int = 30) -> SAEnum:
    """A VARCHAR-backed enum column that stores ``.value`` and returns enum members.

    Without this, ``Mapped[SomeEnum]`` over a plain String column hands back raw
    strings on load — `==` still matches (StrEnum) but `is` checks silently fail.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON}


class Agent(Base):
    """A tenant: an agent runtime with its own API key, webhook, and data.

    Rows scoped to an agent (contacts, conversations, deliveries) are invisible
    to other agents. Admin keys (CONTACTHOP_API_KEYS) and open dev mode see
    everything. agent_id=None rows belong to the admin/dev scope.
    """

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    # sha256 of the API key; plaintext is shown once at creation/rotation.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Overrides the global CONTACTHOP_AGENT_WEBHOOK_URL for this agent's events.
    webhook_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"), index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    identities: Mapped[list[ChannelIdentity]] = relationship(
        back_populates="contact", cascade="all, delete-orphan", lazy="selectin"
    )
    conversations: Mapped[list[Conversation]] = relationship(back_populates="contact")


class ChannelIdentity(Base):
    __tablename__ = "channel_identities"
    # An address belongs to exactly one contact — identity resolution on
    # inbound messages depends on this invariant.
    __table_args__ = (
        UniqueConstraint("channel", "address", name="uq_channel_identity_address"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id"), index=True)
    channel: Mapped[ChannelType] = mapped_column(str_enum(ChannelType))
    # E.164 phone number for sms/voice, email address for email.
    address: Mapped[str] = mapped_column(String(320), index=True)
    verified: Mapped[bool] = mapped_column(default=False)
    # Consent: set by STOP/START keywords (SMS) or unsubscribe flows. Opted-out
    # identities are unreachable — enforced in the gateway backstop.
    opted_out: Mapped[bool] = mapped_column(default=False, server_default=false())

    contact: Mapped[Contact] = relationship(back_populates="identities")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id"), index=True)
    # Denormalized from the contact at creation, for cheap tenant filtering.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"), index=True)
    status: Mapped[ConversationStatus] = mapped_column(
        str_enum(ConversationStatus), default=ConversationStatus.ACTIVE
    )
    current_channel: Mapped[ChannelType] = mapped_column(
        str_enum(ChannelType), default=ChannelType.SMS
    )
    goal: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    contact: Mapped[Contact] = relationship(back_populates="conversations", lazy="selectin")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    events: Mapped[list[ConversationEvent]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationEvent.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    direction: Mapped[Direction] = mapped_column(str_enum(Direction))
    channel: Mapped[ChannelType] = mapped_column(str_enum(ChannelType))
    body: Mapped[str] = mapped_column(Text)
    # Provider identifiers: Twilio MessageSid, email Message-ID, call SID, etc.
    channel_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        str_enum(DeliveryStatus), default=DeliveryStatus.QUEUED
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ChannelSession(Base):
    """A stateful channel attachment — e.g. a live voice call. Cheap channels don't need one."""

    __tablename__ = "channel_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    channel: Mapped[ChannelType] = mapped_column(str_enum(ChannelType))
    state: Mapped[SessionState] = mapped_column(
        str_enum(SessionState), default=SessionState.OPEN
    )
    # Provider handles: call SID, media-stream ids, etc.
    session_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FollowUp(Base):
    """A scheduled no-reply check. Cancelled when the human replies; fired when due."""

    __tablename__ = "follow_ups"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id"))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[FollowUpStatus] = mapped_column(
        str_enum(FollowUpStatus), default=FollowUpStatus.PENDING
    )
    attempt: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentDelivery(Base):
    """Durable outbox for agent webhook notifications.

    Every notification is stored before delivery is attempted; failures retry
    with exponential backoff until EXHAUSTED (dead letter), so a briefly-down
    agent runtime never silently loses an inbound message or follow-up.
    """

    __tablename__ = "agent_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"), index=True)
    event: Mapped[str] = mapped_column(String(80))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        str_enum(WebhookDeliveryStatus), default=WebhookDeliveryStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationEvent(Base):
    __tablename__ = "conversation_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    type: Mapped[EventType] = mapped_column(str_enum(EventType))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="events")
