from __future__ import annotations

from fastapi import HTTPException, Request

from contacthop.channels.sms.twilio import verify_twilio_signature
from contacthop.config import Settings


async def require_twilio_signature(
    request: Request, settings: Settings, form: dict[str, str]
) -> None:
    """Reject the request unless its X-Twilio-Signature is valid.

    No-op when no auth token is configured (console/dev mode). The signature
    covers the full public URL including the query string.
    """
    if not settings.twilio_auth_token:
        return
    signature = request.headers.get("X-Twilio-Signature", "")
    base = settings.public_base_url or str(request.base_url).rstrip("/")
    url = base + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    if not verify_twilio_signature(settings.twilio_auth_token, url, form, signature):
        raise HTTPException(status_code=403, detail="invalid Twilio signature")
