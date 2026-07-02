"""Dev-mode SMS adapter: logs sends instead of hitting a provider."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from contacthop.channels.base import ProviderReceipt
from contacthop.domain.enums import ChannelType

logger = logging.getLogger("contacthop.sms")


class ConsoleSMSAdapter:
    channel = ChannelType.SMS

    async def send(
        self, to_address: str, body: str, meta: dict[str, Any] | None = None
    ) -> ProviderReceipt:
        message_id = f"console-{uuid.uuid4()}"
        logger.info("SMS -> %s: %s", to_address, body)
        return ProviderReceipt(provider_message_id=message_id, meta={"adapter": "console"})
