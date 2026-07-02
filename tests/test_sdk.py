"""SDK: typed client against the real app in-process, and agent dispatch."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from contacthop.config import Settings
from contacthop.domain.enums import ChannelType, DeliveryStatus, Direction
from contacthop.domain.schemas import AgentNotification, MessageRead
from contacthop.main import create_app
from contacthop.sdk import Agent, ContactHopClient, ContactHopError


@pytest.fixture()
async def harness(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, ContactHopClient]]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'sdk.db'}",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    await app.state.db.create_all()
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hop")
    client = ContactHopClient(http_client=http)
    yield app, client
    await client.close()
    await app.state.db.dispose()


async def test_client_full_roundtrip(harness: tuple[FastAPI, ContactHopClient]) -> None:
    _, hop = harness

    contact = await hop.create_contact(
        display_name="Ada",
        identities=[("sms", "+15551234567"), {"channel": "email", "address": "ada@ex.com"}],
    )
    assert {i.channel for i in contact.identities} == {ChannelType.SMS, ChannelType.EMAIL}

    conversation = await hop.create_conversation(contact.id, goal="schedule a demo")
    message = await hop.send(conversation.id, "Does Tuesday work?", follow_up_after=3600)
    assert message.direction is Direction.OUTBOUND
    assert message.channel is ChannelType.SMS
    assert message.delivery_status is DeliveryStatus.SENT

    transcript = await hop.transcript(conversation.id)
    assert [m.body for m in transcript] == ["Does Tuesday work?"]

    switched = await hop.switch(conversation.id, "email", reason="demo")
    assert switched.current_channel is ChannelType.EMAIL

    session = await hop.call(conversation.id, body="Hi Ada!")
    assert session.state.value == "open"
    assert len(await hop.sessions(conversation.id)) == 1

    health = await hop.health()
    assert health["status"] == "ok"


async def test_client_errors_are_typed(harness: tuple[FastAPI, ContactHopClient]) -> None:
    _, hop = harness
    contact = await hop.create_contact(identities=[("email", "solo@ex.com")])
    conversation = await hop.create_conversation(contact.id, channel="email")

    with pytest.raises(ContactHopError) as excinfo:
        await hop.send(conversation.id, "text me", channel="sms")
    assert excinfo.value.status_code == 422
    assert "no sms identity" in excinfo.value.detail


async def test_agent_dispatch_and_reply(harness: tuple[FastAPI, ContactHopClient]) -> None:
    _, hop = harness
    contact = await hop.create_contact(identities=[("sms", "+15559990000")])
    conversation = await hop.create_conversation(contact.id, goal="echo test")

    agent = Agent(client=hop)
    seen: list[MessageRead] = []

    @agent.on_message
    async def handle(ctx, message):  # type: ignore[no-untyped-def]
        seen.append(message)
        await ctx.send(f"You said: {message.body}")

    # simulate the harness pushing an inbound-message notification
    inbound = MessageRead(
        id=conversation.id,
        conversation_id=conversation.id,
        direction=Direction.INBOUND,
        channel=ChannelType.SMS,
        body="hello agent",
        channel_meta={},
        delivery_status=DeliveryStatus.DELIVERED,
        created_at=conversation.created_at,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://agent"
    ) as webhook:
        resp = await webhook.post(
            "/",
            json=AgentNotification(
                event="conversation.message.received",
                conversation_id=conversation.id,
                contact_id=contact.id,
                message=inbound,
            ).model_dump(mode="json"),
        )
    assert resp.status_code == 200
    assert [m.body for m in seen] == ["hello agent"]

    # the handler's ctx.send went through the real gateway
    transcript = await hop.transcript(conversation.id)
    assert transcript[-1].body == "You said: hello agent"
    assert transcript[-1].direction is Direction.OUTBOUND


async def test_agent_follow_up_dispatch(harness: tuple[FastAPI, ContactHopClient]) -> None:
    _, hop = harness
    contact = await hop.create_contact(identities=[("sms", "+15558887777")])
    conversation = await hop.create_conversation(contact.id)

    agent = Agent(client=hop)
    payloads: list[dict] = []

    @agent.on_follow_up
    async def nudge(ctx, payload):  # type: ignore[no-untyped-def]
        payloads.append(payload)

    await agent.dispatch(
        AgentNotification(
            event="conversation.follow_up.due",
            conversation_id=conversation.id,
            contact_id=contact.id,
            payload={"attempt": 1, "no_reply_on": "sms", "suggested_channel": "email"},
        )
    )
    assert payloads == [{"attempt": 1, "no_reply_on": "sms", "suggested_channel": "email"}]


async def test_handler_exceptions_do_not_bounce_webhook(
    harness: tuple[FastAPI, ContactHopClient],
) -> None:
    _, hop = harness
    contact = await hop.create_contact(identities=[("sms", "+15550009999")])
    conversation = await hop.create_conversation(contact.id)

    agent = Agent(client=hop)

    @agent.on_event
    async def broken(ctx, notification):  # type: ignore[no-untyped-def]
        raise RuntimeError("handler bug")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://agent"
    ) as webhook:
        resp = await webhook.post(
            "/",
            json=AgentNotification(
                event="conversation.note",
                conversation_id=conversation.id,
                contact_id=contact.id,
            ).model_dump(mode="json"),
        )
    assert resp.status_code == 200
