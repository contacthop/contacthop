"""SMS consent: STOP/START/HELP keywords and gateway enforcement of opt-out."""

from __future__ import annotations

from fastapi.testclient import TestClient

from contacthop.orchestrator.consent import ConsentAction, classify

PHONE = "+15558675309"


def _setup(client: TestClient, with_email: bool = False) -> str:
    identities = [{"channel": "sms", "address": PHONE}]
    if with_email:
        identities.append({"channel": "email", "address": "jenny@example.com"})
    contact = client.post("/v1/contacts", json={"identities": identities}).json()
    conversation = client.post(
        "/v1/conversations", json={"contact_id": contact["id"], "channel": "sms"}
    ).json()
    return conversation["id"]


def _text_in(client: TestClient, body: str) -> str:
    resp = client.post(
        "/webhooks/twilio/sms",
        data={"From": PHONE, "To": "+15550000000", "Body": body, "MessageSid": "SMx"},
    )
    assert resp.status_code == 200
    return resp.text


def test_keyword_classification() -> None:
    assert classify("STOP") is ConsentAction.OPT_OUT
    assert classify("  stop  ") is ConsentAction.OPT_OUT
    assert classify("Unsubscribe") is ConsentAction.OPT_OUT
    assert classify("START") is ConsentAction.OPT_IN
    assert classify("help") is ConsentAction.HELP
    assert classify("please stop calling me about the demo") is ConsentAction.NONE


def test_stop_blocks_sends_and_calls(client: TestClient) -> None:
    conv = _setup(client)
    twiml = _text_in(client, "STOP")
    assert "<Message>" not in twiml  # carrier sends the mandated confirmation, not us

    send = client.post(f"/v1/conversations/{conv}/messages", json={"body": "wait, one more"})
    assert send.status_code in (403, 422)  # explicit block or no usable channel

    explicit = client.post(
        f"/v1/conversations/{conv}/messages", json={"body": "hello?", "channel": "sms"}
    )
    assert explicit.status_code == 403
    assert "opted out" in explicit.json()["detail"]

    call = client.post(f"/v1/conversations/{conv}/call", json={})
    assert call.status_code == 403


def test_stop_reroutes_policy_to_other_channels(client: TestClient) -> None:
    conv = _setup(client, with_email=True)
    _text_in(client, "STOP")
    resp = client.post(f"/v1/conversations/{conv}/messages", json={"body": "following up"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["channel"] == "email"


def test_start_resubscribes(client: TestClient) -> None:
    conv = _setup(client)
    _text_in(client, "STOP")
    twiml = _text_in(client, "START")
    assert "resubscribed" in twiml

    resp = client.post(
        f"/v1/conversations/{conv}/messages", json={"body": "welcome back", "channel": "sms"}
    )
    assert resp.status_code == 201, resp.text


def test_help_gets_informational_reply(client: TestClient) -> None:
    _setup(client)
    twiml = _text_in(client, "HELP")
    assert "Reply STOP to unsubscribe" in twiml


def test_consent_changes_are_recorded(client: TestClient) -> None:
    conv = _setup(client)
    _text_in(client, "STOP")

    events = client.get(f"/v1/conversations/{conv}/events").json()
    notes = [e["payload"].get("note", "") for e in events if e["type"] == "note"]
    assert any("opt_out" in n for n in notes)

    # the STOP message itself is kept in the transcript for auditability
    transcript = client.get(f"/v1/conversations/{conv}/transcript").json()
    assert transcript[-1]["body"] == "STOP"

    # and the identity state is visible on the contact
    contact_id = client.get(f"/v1/conversations/{conv}").json()["contact_id"]
    contact = client.get(f"/v1/contacts/{contact_id}").json()
    assert contact["identities"][0]["opted_out"] is True
