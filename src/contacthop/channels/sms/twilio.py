"""Twilio SMS adapter using the REST API directly over httpx (no SDK dependency)."""

from __future__ import annotations

import base64
import hashlib
import hmac

import httpx

from contacthop.channels.base import ChannelSendError, ProviderReceipt
from contacthop.domain.enums import ChannelType

TWILIO_API = "https://api.twilio.com/2010-04-01"


class TwilioSMSAdapter:
    channel = ChannelType.SMS

    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number

    async def send(self, to_address: str, body: str) -> ProviderReceipt:
        url = f"{TWILIO_API}/Accounts/{self.account_sid}/Messages.json"
        async with httpx.AsyncClient(auth=(self.account_sid, self.auth_token)) as client:
            resp = await client.post(
                url, data={"To": to_address, "From": self.from_number, "Body": body}
            )
        if resp.status_code >= 400:
            raise ChannelSendError(f"Twilio send failed ({resp.status_code}): {resp.text}")
        payload = resp.json()
        return ProviderReceipt(
            provider_message_id=payload["sid"],
            meta={"status": payload.get("status"), "adapter": "twilio"},
        )


def verify_twilio_signature(
    auth_token: str, url: str, form: dict[str, str], signature: str
) -> bool:
    """Validate an X-Twilio-Signature header per Twilio's HMAC-SHA1 scheme."""
    payload = url + "".join(k + form[k] for k in sorted(form))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)
