"""Phase 2: email channel, threading, channel hops, follow-ups, identity resolution."""

from __future__ import annotations

from fastapi.testclient import TestClient


def make_contact(client: TestClient) -> dict:
    resp = client.post(
        "/v1/contacts",
        json={
            "display_name": "Grace Hopper",
            "identities": [
                {"channel": "sms", "address": "+15557654321"},
                {"channel": "email", "address": "grace@example.com"},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_conversation(client: TestClient, contact_id: str, goal: str = "quarterly report") -> dict:
    resp = client.post(
        "/v1/conversations",
        json={"contact_id": contact_id, "goal": goal, "channel": "sms"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_long_form_hops_to_email_and_threads(client: TestClient) -> None:
    contact = make_contact(client)
    conversation = make_conversation(client, contact["id"])

    long_body = "Here is the full quarterly breakdown. " * 60  # > policy threshold
    first = client.post(
        f"/v1/conversations/{conversation['id']}/messages", json={"body": long_body}
    ).json()
    assert first["channel"] == "email"
    assert first["channel_meta"]["policy_reason"] == "long-form content prefers email"
    assert first["channel_meta"]["subject"] == "quarterly report"
    assert first["channel_meta"]["in_reply_to"] is None

    # conversation moved to email, and the hop was logged
    conv = client.get(f"/v1/conversations/{conversation['id']}").json()
    assert conv["current_channel"] == "email"
    events = client.get(f"/v1/conversations/{conversation['id']}/events").json()
    assert [e["type"] for e in events] == ["channel_switch"]
    assert events[0]["payload"]["to"] == "email"

    # second email threads onto the first
    second = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"body": "One more attachment coming.", "channel": "email"},
    ).json()
    assert second["channel_meta"]["in_reply_to"] == first["channel_meta"]["provider_message_id"]
    assert second["channel_meta"]["subject"] == "Re: quarterly report"
    assert first["channel_meta"]["provider_message_id"] in second["channel_meta"]["references"]


def test_inbound_email_webhook_and_channel_switch_event(client: TestClient) -> None:
    contact = make_contact(client)
    conversation = make_conversation(client, contact["id"])

    resp = client.post(
        "/webhooks/email/inbound",
        json={
            "from_address": "grace@example.com",
            "to_address": "assistant@contacthop.local",
            "subject": "Re: quarterly report",
            "text": "Numbers look good, ship it.",
            "message_id": "<abc123@mail.example.com>",
        },
    )
    assert resp.status_code == 202

    transcript = client.get(f"/v1/conversations/{conversation['id']}/transcript").json()
    assert transcript[-1]["channel"] == "email"
    assert transcript[-1]["body"] == "Numbers look good, ship it."
    # human replied on a different channel than the conversation's current one
    events = client.get(f"/v1/conversations/{conversation['id']}/events").json()
    assert events[0]["type"] == "channel_switch"
    assert events[0]["payload"]["reason"] == "human replied on a different channel"

    # regression: a reply with NO explicit channel must stay on email (enum round-trip
    # from the DB) and thread onto the human's Message-ID
    reply = client.post(
        f"/v1/conversations/{conversation['id']}/messages", json={"body": "Shipping it now."}
    ).json()
    assert reply["channel"] == "email"
    assert reply["channel_meta"]["in_reply_to"] == "<abc123@mail.example.com>"
    assert reply["channel_meta"]["subject"] == "Re: quarterly report"


def test_explicit_switch_endpoint(client: TestClient) -> None:
    contact = make_contact(client)
    conversation = make_conversation(client, contact["id"])

    resp = client.post(
        f"/v1/conversations/{conversation['id']}/switch",
        json={"channel": "email", "reason": "user asked for email"},
    )
    assert resp.status_code == 200
    assert resp.json()["current_channel"] == "email"
    events = client.get(f"/v1/conversations/{conversation['id']}/events").json()
    assert events[0]["payload"]["reason"] == "user asked for email"


def test_add_identity_endpoint(client: TestClient) -> None:
    resp = client.post(
        "/v1/contacts",
        json={"display_name": "Solo", "identities": [{"channel": "sms", "address": "+15550001"}]},
    )
    contact = resp.json()

    added = client.post(
        f"/v1/contacts/{contact['id']}/identities",
        json={"channel": "email", "address": "solo@example.com"},
    )
    assert added.status_code == 201
    channels = {i["channel"] for i in added.json()["identities"]}
    assert channels == {"sms", "email"}

    dup = client.post(
        f"/v1/contacts/{contact['id']}/identities",
        json={"channel": "email", "address": "solo@example.com"},
    )
    assert dup.status_code == 409


def test_follow_up_fires_and_suggests_next_channel(client: TestClient) -> None:
    contact = make_contact(client)
    conversation = make_conversation(client, contact["id"])

    client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"body": "Are we still on for 3pm?", "follow_up_after": 0},
    )

    scheduler = client.app.state.scheduler
    fired = client.portal.call(scheduler.fire_due)
    assert fired == 1

    events = client.get(f"/v1/conversations/{conversation['id']}/events").json()
    escalations = [e for e in events if e["type"] == "escalation"]
    assert len(escalations) == 1
    assert escalations[0]["payload"]["attempt"] == 1
    assert escalations[0]["payload"]["no_reply_on"] == "sms"
    assert escalations[0]["payload"]["suggested_channel"] == "email"

    # already fired — a second sweep is a no-op
    assert client.portal.call(scheduler.fire_due) == 0


def test_follow_up_cancelled_when_human_replies(client: TestClient) -> None:
    contact = make_contact(client)
    conversation = make_conversation(client, contact["id"])

    client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"body": "Ping me back?", "follow_up_after": 0},
    )
    client.post(
        "/webhooks/twilio/sms",
        data={"From": "+15557654321", "To": "+15550000000", "Body": "pong"},
    )

    scheduler = client.app.state.scheduler
    assert client.portal.call(scheduler.fire_due) == 0
    events = client.get(f"/v1/conversations/{conversation['id']}/events").json()
    assert all(e["type"] != "escalation" for e in events)
