"""Twilio Programmable Voice adapter: originates calls via the REST API.

Call audio itself is driven by TwiML served from our voice webhooks — Twilio's
``<Say>`` handles TTS and ``<Gather input="speech">`` handles STT, so v1 voice
needs no separate speech providers. A Media Streams pipeline is the upgrade
path for lower-latency, barge-in-capable conversations.
"""

from __future__ import annotations

from typing import Any

import httpx

from contacthop.channels.base import ChannelSendError, ProviderReceipt
from contacthop.channels.sms.twilio import TWILIO_API
from contacthop.domain.enums import ChannelType


class TwilioVoiceAdapter:
    channel = ChannelType.VOICE

    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number

    async def send(
        self, to_address: str, body: str, meta: dict[str, Any] | None = None
    ) -> ProviderReceipt:
        raise ChannelSendError(
            "voice speech is delivered through the live call session, not send(); "
            "originate a call and queue messages instead"
        )

    async def originate_call(
        self, to_address: str, answer_url: str, status_url: str
    ) -> ProviderReceipt:
        url = f"{TWILIO_API}/Accounts/{self.account_sid}/Calls.json"
        async with httpx.AsyncClient(auth=(self.account_sid, self.auth_token)) as client:
            resp = await client.post(
                url,
                data={
                    "To": to_address,
                    "From": self.from_number,
                    "Url": answer_url,
                    "Method": "POST",
                    "StatusCallback": status_url,
                    "StatusCallbackMethod": "POST",
                },
            )
        if resp.status_code >= 400:
            raise ChannelSendError(f"Twilio call failed ({resp.status_code}): {resp.text}")
        payload = resp.json()
        return ProviderReceipt(
            provider_message_id=payload["sid"],
            meta={"status": payload.get("status"), "adapter": "twilio"},
        )
