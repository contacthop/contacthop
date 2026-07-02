from __future__ import annotations

from contacthop.domain.enums import ChannelType, Urgency
from contacthop.orchestrator.policy import LONG_FORM_THRESHOLD, PolicyContext, decide

ALL = {ChannelType.SMS, ChannelType.EMAIL, ChannelType.VOICE}


def ctx(**overrides: object) -> PolicyContext:
    defaults: dict = {
        "current_channel": ChannelType.SMS,
        "available_channels": ALL,
        "configured_channels": ALL,
    }
    defaults.update(overrides)
    return PolicyContext(**defaults)


def test_explicit_override_wins() -> None:
    decision = decide(ctx(explicit_channel=ChannelType.EMAIL, urgency=Urgency.HIGH))
    assert decision.channel is ChannelType.EMAIL
    assert decision.reason == "agent override"


def test_long_form_prefers_email() -> None:
    decision = decide(ctx(body_length=LONG_FORM_THRESHOLD + 1))
    assert decision.channel is ChannelType.EMAIL


def test_long_form_without_email_identity_stays_put() -> None:
    decision = decide(
        ctx(body_length=LONG_FORM_THRESHOLD + 1, available_channels={ChannelType.SMS})
    )
    assert decision.channel is ChannelType.SMS


def test_high_urgency_prefers_sms() -> None:
    decision = decide(ctx(current_channel=ChannelType.EMAIL, urgency=Urgency.HIGH))
    assert decision.channel is ChannelType.SMS


def test_default_stays_on_current_channel() -> None:
    decision = decide(ctx())
    assert decision.channel is ChannelType.SMS
    assert decision.reason == "stay on current channel"


def test_falls_back_to_contact_preference() -> None:
    decision = decide(
        ctx(
            current_channel=ChannelType.VOICE,
            available_channels={ChannelType.SMS, ChannelType.EMAIL},
            contact_preferred_channel=ChannelType.EMAIL,
        )
    )
    assert decision.channel is ChannelType.EMAIL
