"""Voice call webhooks: the TwiML loop that drives a live call.

Flow: ``/answer`` speaks any queued opening and listens; each human utterance
hits ``/turn`` (Twilio's speech recognition does STT) and is recorded + pushed
to the agent; ``/continue`` polls for the agent's queued reply and speaks it
(``<Say>`` does TTS), then listens again. ``/status`` closes the session when
the call ends.
"""

from __future__ import annotations

import uuid
from xml.sax.saxutils import escape

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from contacthop.api.deps import SessionDep, SettingsDep
from contacthop.api.webhooks.twilio_common import require_twilio_signature
from contacthop.config import Settings
from contacthop.domain.enums import ChannelType, DeliveryStatus, Direction, EventType
from contacthop.domain.models import Conversation, ConversationEvent, Message
from contacthop.domain.schemas import AgentNotification, MessageRead
from contacthop.orchestrator.conversation import cancel_follow_ups, notify_agent
from contacthop.orchestrator.voice import close_open_session, drain_queued_speech

router = APIRouter(prefix="/webhooks/twilio/voice", tags=["webhooks"])

# How many 1-second /continue polls to wait for an agent reply before bowing out.
MAX_SILENT_POLLS = 15

TERMINAL_CALL_STATUSES = {"completed", "busy", "failed", "no-answer", "canceled"}


def _twiml(inner: str) -> Response:
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{inner}</Response>',
        media_type="application/xml",
    )


def _base_url(settings: Settings, request: Request) -> str:
    return settings.public_base_url or str(request.base_url).rstrip("/")


def _listen(base: str, conversation_id: uuid.UUID) -> str:
    """Gather a spoken turn; if the human stays silent, fall through to /continue."""
    turn = f"{base}/webhooks/twilio/voice/turn?conversation_id={conversation_id}"
    cont = f"{base}/webhooks/twilio/voice/continue?conversation_id={conversation_id}&amp;polls=0"
    return (
        f'<Gather input="speech" speechTimeout="auto" method="POST" action="{escape(turn)}"/>'
        f'<Redirect method="POST">{cont}</Redirect>'
    )


def _say(messages: list[Message]) -> str:
    return "".join(f"<Say>{escape(m.body)}</Say>" for m in messages)


async def _get_conversation(session: SessionDep, conversation_id: uuid.UUID) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conversation


@router.post("/answer")
async def answer(
    conversation_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items()}
    await require_twilio_signature(request, settings, form)
    await _get_conversation(session, conversation_id)

    queued = await drain_queued_speech(session, conversation_id)
    base = _base_url(settings, request)
    return _twiml(_say(queued) + _listen(base, conversation_id))


@router.post("/turn")
async def turn(
    conversation_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    background: BackgroundTasks,
) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items()}
    await require_twilio_signature(request, settings, form)
    conversation = await _get_conversation(session, conversation_id)
    base = _base_url(settings, request)

    speech = form.get("SpeechResult", "").strip()
    if speech:
        message = Message(
            conversation_id=conversation.id,
            direction=Direction.INBOUND,
            channel=ChannelType.VOICE,
            body=speech,
            channel_meta={
                "call_sid": form.get("CallSid"),
                "confidence": form.get("Confidence"),
            },
            delivery_status=DeliveryStatus.DELIVERED,
        )
        session.add(message)
        await cancel_follow_ups(session, conversation.id)
        await session.flush()
        background.add_task(
            notify_agent,
            settings,
            AgentNotification(
                event="conversation.message.received",
                conversation_id=conversation.id,
                contact_id=conversation.contact_id,
                message=MessageRead.model_validate(message),
            ),
        )

    cont = f"{base}/webhooks/twilio/voice/continue?conversation_id={conversation_id}&amp;polls=0"
    return _twiml(f'<Pause length="1"/><Redirect method="POST">{cont}</Redirect>')


@router.post("/continue")
async def continue_call(
    conversation_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    polls: int = 0,
) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items()}
    await require_twilio_signature(request, settings, form)
    await _get_conversation(session, conversation_id)
    base = _base_url(settings, request)

    queued = await drain_queued_speech(session, conversation_id)
    if queued:
        return _twiml(_say(queued) + _listen(base, conversation_id))

    if polls < MAX_SILENT_POLLS:
        cont = (
            f"{base}/webhooks/twilio/voice/continue"
            f"?conversation_id={conversation_id}&amp;polls={polls + 1}"
        )
        return _twiml(f'<Pause length="1"/><Redirect method="POST">{cont}</Redirect>')

    # Agent didn't reply in time — end gracefully and let the conversation hop channels.
    await close_open_session(session, conversation_id, reason="agent reply timeout")
    session.add(
        ConversationEvent(
            conversation_id=conversation_id,
            type=EventType.NOTE,
            payload={"note": "call ended waiting for agent reply"},
        )
    )
    return _twiml("<Say>I will follow up with you over text shortly. Goodbye.</Say><Hangup/>")


@router.post("/status")
async def status(
    conversation_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, str]:
    form = {k: str(v) for k, v in (await request.form()).items()}
    await require_twilio_signature(request, settings, form)

    call_status = form.get("CallStatus", "")
    if call_status in TERMINAL_CALL_STATUSES:
        closed = await close_open_session(
            session, conversation_id, reason=f"call {call_status}"
        )
        if closed is not None:
            session.add(
                ConversationEvent(
                    conversation_id=conversation_id,
                    type=EventType.NOTE,
                    payload={"note": f"voice call ended ({call_status})"},
                )
            )
    return {"status": "ok"}
