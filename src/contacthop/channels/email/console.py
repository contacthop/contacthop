"""Dev-mode email adapter: logs sends instead of hitting a provider."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from contacthop.channels.base import ProviderReceipt
from contacthop.domain.enums import ChannelType

logger = logging.getLogger("contacthop.email")


class ConsoleEmailAdapter:
    channel = ChannelType.EMAIL

    async def send(
        self, to_address: str, body: str, meta: dict[str, Any] | None = None
    ) -> ProviderReceipt:
        meta = meta or {}
        subject = meta.get("subject", "(no subject)")
        message_id = f"<console-{uuid.uuid4()}@contacthop.local>"
        logger.info("EMAIL -> %s [%s]: %s", to_address, subject, body)
        return ProviderReceipt(
            provider_message_id=message_id,
            meta={
                "adapter": "console",
                "subject": subject,
                "in_reply_to": meta.get("in_reply_to"),
                "references": meta.get("references", []),
            },
        )
