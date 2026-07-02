"""Inbound SMS webhook (Twilio Messaging).

Verifies the provider signature when Twilio credentials are configured, normalizes
the payload into an InboundMessage, records it, and notifies the agent runtime.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from contacthop.api.deps import SessionDep, SettingsDep
from contacthop.api.webhooks.twilio_common import require_twilio_signature
from contacthop.domain.enums import ChannelType
from contacthop.domain.schemas import InboundMessage
from contacthop.orchestrator.conversation import (
    inbound_notification,
    notify_agent,
    record_inbound,
    resolve_identity,
)

router = APIRouter(prefix="/webhooks/twilio", tags=["webhooks"])


@router.post("/sms")
async def inbound_sms(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    background: BackgroundTasks,
) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items()}
    await require_twilio_signature(request, settings, form)

    if not form.get("From") or "Body" not in form:
        raise HTTPException(status_code=400, detail="missing From or Body")

    inbound = InboundMessage(
        channel=ChannelType.SMS,
        from_address=form["From"],
        to_address=form.get("To", ""),
        body=form["Body"],
        provider_message_id=form.get("MessageSid"),
    )
    message = await record_inbound(session, inbound)
    identity = await resolve_identity(session, inbound.channel, inbound.from_address)
    notification = inbound_notification(message, identity.contact_id)
    # Commit before the notification runs: the agent may synchronously call
    # back into the API, which must not collide with this open transaction.
    await session.commit()
    background.add_task(notify_agent, settings, notification)

    # Empty TwiML: acknowledge without auto-replying; the agent decides the reply.
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )
