from __future__ import annotations

from contacthop.domain.enums import ChannelType
from contacthop.orchestrator.escalation import next_channel

ALL = {ChannelType.SMS, ChannelType.EMAIL, ChannelType.VOICE}


def test_ladder_climbs_from_sms_to_email() -> None:
    assert next_channel(ChannelType.SMS, ALL, ALL) is ChannelType.EMAIL


def test_ladder_climbs_from_email_to_voice() -> None:
    assert next_channel(ChannelType.EMAIL, ALL, ALL) is ChannelType.VOICE


def test_ladder_wraps_from_voice_back_to_sms() -> None:
    assert next_channel(ChannelType.VOICE, ALL, ALL) is ChannelType.SMS


def test_skips_channels_without_identity() -> None:
    assert (
        next_channel(ChannelType.SMS, {ChannelType.SMS, ChannelType.VOICE}, ALL)
        is ChannelType.VOICE
    )


def test_skips_unconfigured_channels() -> None:
    assert (
        next_channel(ChannelType.SMS, ALL, {ChannelType.SMS, ChannelType.EMAIL})
        is ChannelType.EMAIL
    )


def test_stays_put_when_nothing_else_usable() -> None:
    assert next_channel(ChannelType.SMS, {ChannelType.SMS}, ALL) is ChannelType.SMS
    assert next_channel(ChannelType.SMS, set(), ALL) is ChannelType.SMS
