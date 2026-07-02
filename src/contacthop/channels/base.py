"""Channel adapter contract. The rest of the system never imports a provider SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from contacthop.domain.enums import ChannelType


@dataclass
class ProviderReceipt:
    provider_message_id: str
    meta: dict[str, Any] = field(default_factory=dict)


class ChannelSendError(Exception):
    """Raised when a provider rejects or fails an outbound send."""


class ChannelAdapter(Protocol):
    channel: ChannelType

    async def send(
        self, to_address: str, body: str, meta: dict[str, Any] | None = None
    ) -> ProviderReceipt:
        """Deliver ``body`` to ``to_address``.

        ``meta`` carries channel-specific hints (email subject and threading
        headers, etc.); adapters ignore keys they don't understand.
        """
        ...


class VoiceAdapter(ChannelAdapter, Protocol):
    """Voice is session-based: calls are originated, then speech flows through the
    live session (queued messages drained by the call webhooks), not ``send()``."""

    async def originate_call(
        self, to_address: str, answer_url: str, status_url: str
    ) -> ProviderReceipt: ...
