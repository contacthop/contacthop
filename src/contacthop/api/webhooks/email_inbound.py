"""Generic inbound email webhook.

Accepts a normalized JSON payload so any provider (SendGrid Inbound Parse, SES,
Postmark, Mailgun) can be bridged with a tiny mapping layer. Optionally guarded
by a shared secret (``CONTACTHOP_EMAIL_INBOUND_TOKEN``).
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from contacthop.api.deps import SessionDep, SettingsDep
from contacthop.domain.enums import ChannelType
from contacthop.domain.models import Contact
from contacthop.domain.schemas import EmailInboundPayload, InboundMessage
from contacthop.orchestrator.conversation import (
    inbound_notification,
    record_inbound,
    resolve_identity,
)
from contacthop.orchestrator.notifier import attempt_delivery, enqueue_notification

router = APIRouter(prefix="/webhooks/email", tags=["webhooks"])


@router.post("/inbound", status_code=202)
async def inbound_email(
    payload: EmailInboundPayload,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    background: BackgroundTasks,
    x_contacthop_token: str | None = Header(default=None),
) -> dict[str, str]:
    if settings.email_inbound_token and x_contacthop_token != settings.email_inbound_token:
        raise HTTPException(status_code=403, detail="invalid inbound token")

    inbound = InboundMessage(
        channel=ChannelType.EMAIL,
        from_address=payload.from_address,
        to_address=payload.to_address,
        body=payload.text,
        provider_message_id=payload.message_id,
        channel_meta={
            "subject": payload.subject,
            "in_reply_to": payload.in_reply_to,
        },
    )
    message = await record_inbound(session, inbound)
    identity = await resolve_identity(session, inbound.channel, inbound.from_address)
    owner = await session.get(Contact, identity.contact_id)
    # Durable outbox: stored with this transaction, delivered in the background,
    # retried by the scheduler if the agent is unreachable.
    delivery = await enqueue_notification(
        session,
        settings,
        inbound_notification(message, identity.contact_id),
        agent_id=owner.agent_id if owner else None,
    )
    await session.commit()
    if delivery is not None:
        background.add_task(attempt_delivery, request.app.state.db, settings, delivery.id)
    return {"status": "accepted"}
