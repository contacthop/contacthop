"""Dev-mode voice adapter: logs call origination instead of dialing.

Drive the call lifecycle by posting to the voice webhooks directly
(answer / turn / continue / status) — exactly what the tests do.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from contacthop.channels.base import ProviderReceipt
from contacthop.domain.enums import ChannelType

logger = logging.getLogger("contacthop.voice")


class ConsoleVoiceAdapter:
    channel = ChannelType.VOICE

    async def send(
        self, to_address: str, body: str, meta: dict[str, Any] | None = None
    ) -> ProviderReceipt:
        logger.info("VOICE (speak) -> %s: %s", to_address, body)
        return ProviderReceipt(f"console-speech-{uuid.uuid4()}", {"adapter": "console"})

    async def originate_call(
        self, to_address: str, answer_url: str, status_url: str
    ) -> ProviderReceipt:
        call_sid = f"console-call-{uuid.uuid4()}"
        logger.info("VOICE (call) -> %s (answer: %s)", to_address, answer_url)
        return ProviderReceipt(call_sid, {"adapter": "console"})
