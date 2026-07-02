"""Inbound SMS webhook (Twilio Messaging).

Verifies the provider signature when Twilio credentials are configured, normalizes
the payload into an InboundMessage, records it, and notifies the agent runtime.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from sqlalchemy import select

from contacthop.api.deps import SessionDep, SettingsDep
from contacthop.api.webhooks.twilio_common import require_twilio_signature
from contacthop.domain.enums import ChannelType, DeliveryStatus, Direction, EventType
from contacthop.domain.models import Conversation, ConversationEvent, Message
from contacthop.domain.schemas import AgentNotification, InboundMessage
from contacthop.orchestrator.conversation import (
    inbound_notification,
    notify_agent,
    record_inbound,
    resolve_identity,
)

# Twilio MessageStatus -> our delivery status. Intermediate states are skipped.
STATUS_MAP = {
    "sent": DeliveryStatus.SENT,
    "delivered": DeliveryStatus.DELIVERED,
    "read": DeliveryStatus.READ,
    "undelivered": DeliveryStatus.FAILED,
    "failed": DeliveryStatus.FAILED,
}

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


@router.post("/sms/status")
async def sms_delivery_status(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    background: BackgroundTasks,
) -> dict[str, str]:
    """Delivery receipts: update Message.delivery_status; tell the agent on failure."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    await require_twilio_signature(request, settings, form)

    sid = form.get("MessageSid")
    status = STATUS_MAP.get(form.get("MessageStatus", "").lower())
    if not sid or status is None:
        return {"status": "ignored"}

    result = await session.execute(
        select(Message).where(
            Message.direction == Direction.OUTBOUND,
            Message.channel_meta["provider_message_id"].as_string() == sid,
        )
    )
    message = result.scalars().first()
    if message is None:
        return {"status": "unknown message"}

    message.delivery_status = status
    if status is DeliveryStatus.FAILED:
        conversation = await session.get(Conversation, message.conversation_id)
        session.add(
            ConversationEvent(
                conversation_id=message.conversation_id,
                type=EventType.NOTE,
                payload={"note": "delivery failed", "provider_message_id": sid},
            )
        )
        if conversation is not None:
            notification = AgentNotification(
                event="conversation.message.failed",
                conversation_id=message.conversation_id,
                contact_id=conversation.contact_id,
                payload={
                    "message_id": str(message.id),
                    "channel": message.channel,
                    "provider_status": form.get("MessageStatus"),
                },
            )
            await session.commit()
            background.add_task(notify_agent, settings, notification)
    return {"status": "ok"}
