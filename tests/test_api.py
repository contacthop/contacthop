from __future__ import annotations

from fastapi.testclient import TestClient


def make_contact(client: TestClient) -> dict:
    resp = client.post(
        "/v1/contacts",
        json={
            "display_name": "Ada Lovelace",
            "identities": [{"channel": "sms", "address": "+15551234567"}],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_conversation(client: TestClient, contact_id: str) -> dict:
    resp = client.post(
        "/v1/conversations",
        json={"contact_id": contact_id, "goal": "schedule a demo", "channel": "sms"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_agent_send_and_transcript_roundtrip(client: TestClient) -> None:
    contact = make_contact(client)
    conversation = make_conversation(client, contact["id"])

    send = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"body": "Hi Ada — following up about the demo."},
    )
    assert send.status_code == 201, send.text
    sent = send.json()
    assert sent["direction"] == "outbound"
    assert sent["channel"] == "sms"
    assert sent["delivery_status"] == "sent"
    assert sent["channel_meta"]["policy_reason"] == "stay on current channel"

    transcript = client.get(f"/v1/conversations/{conversation['id']}/transcript")
    assert transcript.status_code == 200
    bodies = [m["body"] for m in transcript.json()]
    assert bodies == ["Hi Ada — following up about the demo."]


def test_send_to_unconfigured_channel_is_rejected(client: TestClient) -> None:
    contact = make_contact(client)
    conversation = make_conversation(client, contact["id"])

    resp = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"body": "long report attached", "channel": "email"},
    )
    assert resp.status_code == 422
    assert "no adapter configured" in resp.json()["detail"]


def test_inbound_sms_webhook_known_contact(client: TestClient) -> None:
    contact = make_contact(client)
    conversation = make_conversation(client, contact["id"])

    resp = client.post(
        "/webhooks/twilio/sms",
        data={
            "From": "+15551234567",
            "To": "+15550000000",
            "Body": "Tuesday at 3pm works!",
            "MessageSid": "SM123",
        },
    )
    assert resp.status_code == 200
    assert "Response" in resp.text  # empty TwiML ack

    transcript = client.get(f"/v1/conversations/{conversation['id']}/transcript").json()
    assert len(transcript) == 1
    inbound = transcript[0]
    assert inbound["direction"] == "inbound"
    assert inbound["body"] == "Tuesday at 3pm works!"
    assert inbound["channel_meta"]["provider_message_id"] == "SM123"


def test_inbound_sms_from_unknown_sender_creates_provisional_contact(
    client: TestClient,
) -> None:
    resp = client.post(
        "/webhooks/twilio/sms",
        data={"From": "+15559999999", "To": "+15550000000", "Body": "who is this?"},
    )
    assert resp.status_code == 200


def test_inbound_missing_fields_rejected(client: TestClient) -> None:
    resp = client.post("/webhooks/twilio/sms", data={"Body": "no sender"})
    assert resp.status_code == 400
