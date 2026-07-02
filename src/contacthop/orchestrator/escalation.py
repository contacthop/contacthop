"""Escalation ladder: which channel to try next when the human isn't replying.

The time-driven half of the policy engine. The scheduler fires no-reply
follow-ups and asks this module for the next rung; the *agent* decides what
to actually send — ContactHop only suggests and notifies.
"""

from __future__ import annotations

from contacthop.domain.enums import ChannelType

# Cheapest-interruption first; voice is the last resort.
LADDER = [ChannelType.SMS, ChannelType.EMAIL, ChannelType.VOICE]


def next_channel(
    current: ChannelType,
    available: set[ChannelType],
    configured: set[ChannelType],
) -> ChannelType:
    """Next rung after ``current`` that the contact has an identity for and we can send on."""
    usable = available & configured
    if not usable:
        return current
    start = LADDER.index(current) + 1 if current in LADDER else 0
    for channel in LADDER[start:] + LADDER[:start]:
        if channel in usable and channel != current:
            return channel
    return current
