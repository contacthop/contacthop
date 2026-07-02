"""Channel policy engine: a pure, testable decision over explicit signals.

Every ``decide()`` call answers one question — "which channel should this
outbound message use right now?" — and returns the reason, which the
orchestrator records as a ConversationEvent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contacthop.domain.enums import ChannelType, Urgency

# Beyond this, a message is long-form and reads better as email.
LONG_FORM_THRESHOLD = 1200


@dataclass
class PolicyContext:
    current_channel: ChannelType
    available_channels: set[ChannelType]
    body_length: int = 0
    urgency: Urgency = Urgency.NORMAL
    explicit_channel: ChannelType | None = None
    contact_preferred_channel: ChannelType | None = None
    configured_channels: set[ChannelType] = field(default_factory=set)
    # Median seconds-to-reply per channel for this contact (absent = no data yet).
    responsiveness: dict[ChannelType, float] = field(default_factory=dict)


@dataclass
class ChannelDecision:
    channel: ChannelType
    reason: str


def decide(ctx: PolicyContext) -> ChannelDecision:
    usable = ctx.available_channels & ctx.configured_channels

    if ctx.explicit_channel is not None:
        return ChannelDecision(ctx.explicit_channel, "agent override")

    if ctx.body_length > LONG_FORM_THRESHOLD and ChannelType.EMAIL in usable:
        return ChannelDecision(ChannelType.EMAIL, "long-form content prefers email")

    if ctx.urgency is Urgency.HIGH:
        measured = {c: s for c, s in ctx.responsiveness.items() if c in usable}
        if measured:
            fastest = min(measured, key=lambda c: measured[c])
            return ChannelDecision(
                fastest, "high urgency prefers the contact's fastest channel"
            )
        if ChannelType.SMS in usable:
            return ChannelDecision(ChannelType.SMS, "high urgency prefers sms")

    if ctx.current_channel in usable:
        return ChannelDecision(ctx.current_channel, "stay on current channel")

    if ctx.contact_preferred_channel in usable:
        assert ctx.contact_preferred_channel is not None
        return ChannelDecision(ctx.contact_preferred_channel, "contact preference")

    if usable:
        return ChannelDecision(sorted(usable)[0], "only usable channel")

    return ChannelDecision(ctx.current_channel, "no usable channel; defaulting to current")
