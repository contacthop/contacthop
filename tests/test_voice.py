"""Phase 3: voice sessions, the TwiML call loop, and queued-speech delivery."""

from __future__ import annotations

from fastapi.testclient import TestClient


def setup_conversation(client: TestClient) -> tuple[str, str]:
    contact = client.post(
        "/v1/contacts",
        json={
            "display_name": "Alan Turing",
            "identities": [
                {"channel": "sms", "address": "+15551112222"},
                {"channel": "email", "address": "alan@example.com"},
            ],
        },
    ).json()
    conversation = client.post(
        "/v1/conversations",
        json={"contact_id": contact["id"], "goal": "afternoon check-in", "channel": "sms"},
    ).json()
    return contact["id"], conversation["id"]


def test_originate_call_opens_session_and_switches_channel(client: TestClient) -> None:
    _, conv = setup_conversation(client)

    resp = client.post(f"/v1/conversations/{conv}/call", json={"body": "Hi Alan, got a minute?"})
    assert resp.status_code == 201, resp.text
    session = resp.json()
    assert session["state"] == "open"
    assert session["channel"] == "voice"
    assert session["session_meta"]["call_sid"].startswith("console-call-")

    conversation = client.get(f"/v1/conversations/{conv}").json()
    assert conversation["current_channel"] == "voice"
    events = client.get(f"/v1/conversations/{conv}/events").json()
    assert events[0]["type"] == "channel_switch"
    assert events[0]["payload"]["reason"] == "call originated"

    # only one live call at a time
    dup = client.post(f"/v1/conversations/{conv}/call", json={})
    assert dup.status_code == 409


def test_answer_speaks_opening_line_and_listens(client: TestClient) -> None:
    _, conv = setup_conversation(client)
    client.post(f"/v1/conversations/{conv}/call", json={"body": "Hi Alan, got a minute?"})

    twiml = client.post(f"/webhooks/twilio/voice/answer?conversation_id={conv}").text
    assert "<Say>Hi Alan, got a minute?</Say>" in twiml
    assert '<Gather input="speech"' in twiml

    # opening line is now SENT in the transcript, not queued
    transcript = client.get(f"/v1/conversations/{conv}/transcript").json()
    assert transcript[0]["channel"] == "voice"
    assert transcript[0]["delivery_status"] == "sent"


def test_turn_records_speech_and_continue_speaks_agent_reply(client: TestClient) -> None:
    _, conv = setup_conversation(client)
    client.post(f"/v1/conversations/{conv}/call", json={"body": "Hi!"})
    client.post(f"/webhooks/twilio/voice/answer?conversation_id={conv}")

    # human speaks; Twilio posts the recognized text
    twiml = client.post(
        f"/webhooks/twilio/voice/turn?conversation_id={conv}",
        data={"SpeechResult": "Sure, what's up?", "CallSid": "CA1", "Confidence": "0.92"},
    ).text
    assert "<Redirect" in twiml

    transcript = client.get(f"/v1/conversations/{conv}/transcript").json()
    inbound = [m for m in transcript if m["direction"] == "inbound"]
    assert inbound[0]["channel"] == "voice"
    assert inbound[0]["body"] == "Sure, what's up?"

    # agent replies while the call is open -> queued for the call loop
    reply = client.post(
        f"/v1/conversations/{conv}/messages", json={"body": "Can we move the demo to 4pm?"}
    ).json()
    assert reply["channel"] == "voice"
    assert reply["delivery_status"] == "queued"

    # the call's poll loop picks it up and speaks it
    twiml = client.post(f"/webhooks/twilio/voice/continue?conversation_id={conv}&polls=0").text
    assert "<Say>Can we move the demo to 4pm?</Say>" in twiml
    assert '<Gather input="speech"' in twiml


def test_continue_gives_up_after_max_polls_and_closes_session(client: TestClient) -> None:
    _, conv = setup_conversation(client)
    client.post(f"/v1/conversations/{conv}/call", json={})

    twiml = client.post(f"/webhooks/twilio/voice/continue?conversation_id={conv}&polls=99").text
    assert "<Hangup/>" in twiml

    sessions = client.get(f"/v1/conversations/{conv}/sessions").json()
    assert sessions[0]["state"] == "closed"
    assert sessions[0]["session_meta"]["close_reason"] == "agent reply timeout"


def test_status_callback_closes_session_and_allows_new_call(client: TestClient) -> None:
    _, conv = setup_conversation(client)
    client.post(f"/v1/conversations/{conv}/call", json={})

    resp = client.post(
        f"/webhooks/twilio/voice/status?conversation_id={conv}",
        data={"CallStatus": "completed", "CallSid": "CA1"},
    )
    assert resp.status_code == 200
    sessions = client.get(f"/v1/conversations/{conv}/sessions").json()
    assert sessions[0]["state"] == "closed"

    # conversation can dial again
    assert client.post(f"/v1/conversations/{conv}/call", json={}).status_code == 201


def test_voice_send_without_open_session_rejected(client: TestClient) -> None:
    _, conv = setup_conversation(client)
    resp = client.post(
        f"/v1/conversations/{conv}/messages",
        json={"body": "hello?", "channel": "voice"},
    )
    assert resp.status_code == 422
    assert "no open voice session" in resp.json()["detail"]


def test_xml_escaping_in_say(client: TestClient) -> None:
    _, conv = setup_conversation(client)
    client.post(f"/v1/conversations/{conv}/call", json={"body": "Tom & Jerry <3"})
    twiml = client.post(f"/webhooks/twilio/voice/answer?conversation_id={conv}").text
    assert "<Say>Tom &amp; Jerry &lt;3</Say>" in twiml