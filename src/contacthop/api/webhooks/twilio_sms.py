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
from contacthop.domain.models import Contact, Conversation, ConversationEvent, Message
from contacthop.domain.schemas import AgentNotification, InboundMessage
from contacthop.orchestrator.consent import ConsentAction, classify
from contacthop.orchestrator.conversation import (
    inbound_notification,
    record_inbound,
    resolve_identity,
)
from contacthop.orchestrator.notifier import attempt_delivery, enqueue_notification


def _twiml_reply(text: str | None = None) -> Response:
    from xml.sax.saxutils import escape

    inner = f"<Message>{escape(text)}</Message>" if text else ""
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{inner}</Response>',
        media_type="application/xml",
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
    owner = await session.get(Contact, identity.contact_id)
    owner_agent_id = owner.agent_id if owner else None

    # Consent keywords take effect before the agent ever sees the message.
    action = classify(inbound.body)
    if action in (ConsentAction.OPT_OUT, ConsentAction.OPT_IN):
        identity.opted_out = action is ConsentAction.OPT_OUT
        session.add(
            ConversationEvent(
                conversation_id=message.conversation_id,
                type=EventType.NOTE,
                payload={"note": f"contact {action} via keyword", "address": identity.address},
            )
        )
        notification = AgentNotification(
            event=f"conversation.contact.{action}",
            conversation_id=message.conversation_id,
            contact_id=identity.contact_id,
            payload={"channel": "sms", "address": identity.address},
        )
        delivery = await enqueue_notification(
            session, settings, notification, agent_id=owner_agent_id
        )
        await session.commit()
        if delivery is not None:
            background.add_task(attempt_delivery, request.app.state.db, settings, delivery.id)
        # STOP: no app-level reply — the carrier sends the mandated confirmation
        # and blocks further traffic. START: confirm resubscription.
        return _twiml_reply(
            settings.sms_opt_in_reply if action is ConsentAction.OPT_IN else None
        )
    if action is ConsentAction.HELP:
        await session.commit()
        return _twiml_reply(settings.sms_help_reply)

    # Durable outbox: stored with this transaction, delivered in the background,
    # retried by the scheduler if the agent is unreachable.
    delivery = await enqueue_notification(
        session,
        settings,
        inbound_notification(message, identity.contact_id),
        agent_id=owner_agent_id,
    )
    await session.commit()
    if delivery is not None:
        background.add_task(attempt_delivery, request.app.state.db, settings, delivery.id)

    # Empty TwiML: acknowledge without auto-replying; the agent decides the reply.
    return _twiml_reply()


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
            delivery = await enqueue_notification(
                session, settings, notification, agent_id=conversation.agent_id
            )
            await session.commit()
            if delivery is not None:
                background.add_task(
                    attempt_delivery, request.app.state.db, settings, delivery.id
                )
    return {"status": "ok"}
