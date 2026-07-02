"""Per-channel send windows (quiet hours).

A window like ``08:00-17:00`` means the channel may only be used inside those
hours, evaluated in the contact's timezone (``preferences["timezone"]``),
falling back to the deployment default. Windows wrap midnight (``21:00-08:00``
allows overnight). No window configured = always allowed.

Precedence per channel: contact ``preferences["send_windows"][channel]``
overrides the deployment-wide ``CONTACTHOP_SEND_WINDOW_<CHANNEL>``.

This is the hard backstop below the policy engine: the gateway filters closed
channels out of policy input and refuses explicit sends on closed channels —
even a buggy agent can't text someone at 3am. The one exemption is a live
voice call: if the human is already on the line, speaking is always allowed.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from datetime import time as dtime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from contacthop.domain.enums import ChannelType

if TYPE_CHECKING:
    from contacthop.config import Settings
    from contacthop.domain.models import Contact

ALWAYS = {"", "always", "any", "24/7"}


def parse_window(spec: str) -> tuple[dtime, dtime]:
    """Parse ``"HH:MM-HH:MM"``; raises ValueError on malformed specs."""
    start_s, sep, end_s = spec.partition("-")
    if not sep:
        raise ValueError(f"send window must look like 'HH:MM-HH:MM', got {spec!r}")
    return dtime.fromisoformat(start_s.strip()), dtime.fromisoformat(end_s.strip())


def window_open(spec: str | None, at: dtime) -> bool:
    if spec is None or spec.strip().lower() in ALWAYS:
        return True
    start, end = parse_window(spec)
    if start == end:
        return False  # zero-length window: channel is never open
    if start < end:
        return start <= at < end
    return at >= start or at < end  # wraps midnight


def contact_timezone(settings: Settings, contact: Contact) -> ZoneInfo:
    name = (contact.preferences or {}).get("timezone") or settings.default_timezone
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


def channel_window(settings: Settings, contact: Contact, channel: ChannelType) -> str | None:
    overrides = (contact.preferences or {}).get("send_windows", {})
    if channel.value in overrides:
        window: str | None = overrides[channel.value]
        return window
    return getattr(settings, f"send_window_{channel.value}", None)


def open_channels(
    settings: Settings,
    contact: Contact,
    channels: Iterable[ChannelType],
    now: datetime | None = None,
) -> set[ChannelType]:
    """The subset of ``channels`` currently inside their send window."""
    local = (now or datetime.now(contact_timezone(settings, contact))).time()
    result = set()
    for channel in channels:
        try:
            if window_open(channel_window(settings, contact, channel), local):
                result.add(channel)
        except ValueError:
            # A malformed per-contact override must not block sends entirely.
            result.add(channel)
    return result
