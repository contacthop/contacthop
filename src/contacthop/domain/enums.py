from __future__ import annotations

from enum import StrEnum


class ChannelType(StrEnum):
    SMS = "sms"
    EMAIL = "email"
    VOICE = "voice"


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class DeliveryStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    READ = "read"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


class EventType(StrEnum):
    MESSAGE = "message"
    CHANNEL_SWITCH = "channel_switch"
    ESCALATION = "escalation"
    TIMEOUT = "timeout"
    NOTE = "note"


class FollowUpStatus(StrEnum):
    PENDING = "pending"
    FIRED = "fired"
    CANCELLED = "cancelled"


class Urgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
