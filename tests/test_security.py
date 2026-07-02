"""API-key auth, per-contact rate limiting, and delivery-receipt ingestion."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contacthop.config import Settings
from contacthop.main import create_app

# ── API keys ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def locked_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'locked.db'}",
        api_keys="secret-key-1, secret-key-2",
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_management_api_requires_key(locked_client: TestClient) -> None:
    resp = locked_client.post("/v1/contacts", json={})
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"

    resp = locked_client.post(
        "/v1/contacts", json={}, headers={"Authorization": "Bearer wrong-key"}
    )
    assert resp.status_code == 401


def test_any_configured_key_works(locked_client: TestClient) -> None:
    for key in ("secret-key-1", "secret-key-2"):
        resp = locked_client.post(
            "/v1/contacts",
            json={"display_name": f"via {key}"},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert resp.status_code == 201, resp.text


def test_webhooks_and_health_stay_open(locked_client: TestClient) -> None:
    assert locked_client.get("/health").status_code == 200
    resp = locked_client.post(
        "/webhooks/twilio/sms",
        data={"From": "+15550001111", "To": "+15550000000", "Body": "no bearer needed"},
    )
    assert resp.status_code == 200


def test_no_keys_configured_means_open(client: TestClient) -> None:
    assert client.post("/v1/contacts", json={}).status_code == 201


# ── rate limiting ────────────────────────────────────────────────────────────


@pytest.fixture()
def limited_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'limited.db'}",
        max_messages_per_hour=3,
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _conversation(client: TestClient, preferences: dict | None = None) -> str:
    contact = client.post(
        "/v1/contacts",
        json={
            "identities": [{"channel": "sms", "address": "+15551119999"}],
            "preferences": preferences or {},
        },
    ).json()
    return client.post("/v1/conversations", json={"contact_id": contact["id"]}).json()["id"]


def test_rate_limit_blocks_fourth_message(limited_client: TestClient) -> None:
    conv = _conversation(limited_client)
    for i in range(3):
        assert (
            limited_client.post(
                f"/v1/conversations/{conv}/messages", json={"body": f"msg {i}"}
            ).status_code
            == 201
        )
    resp = limited_client.post(f"/v1/conversations/{conv}/messages", json={"body": "msg 3"})
    assert resp.status_code == 429
    assert "rate limit" in resp.json()["detail"]

    # calls count against the same budget
    assert limited_client.post(f"/v1/conversations/{conv}/call", json={}).status_code == 429


def test_contact_preference_overrides_rate_limit(limited_client: TestClient) -> None:
    conv = _conversation(limited_client, preferences={"max_messages_per_hour": 5})
    for i in range(5):
        assert (
            limited_client.post(
                f"/v1/conversations/{conv}/messages", json={"body": f"msg {i}"}
            ).status_code
            == 201
        )
    assert (
        limited_client.post(f"/v1/conversations/{conv}/messages", json={"body": "over"})
        .status_code
        == 429
    )


def test_inbound_messages_do_not_count(limited_client: TestClient) -> None:
    conv = _conversation(limited_client)
    for _ in range(5):  # human can text as much as they like
        limited_client.post(
            "/webhooks/twilio/sms",
            data={"From": "+15551119999", "To": "+15550000000", "Body": "spam from human"},
        )
    resp = limited_client.post(f"/v1/conversations/{conv}/messages", json={"body": "reply"})
    assert resp.status_code == 201


# ── delivery receipts ────────────────────────────────────────────────────────


def test_delivery_status_callback_updates_message(client: TestClient) -> None:
    contact = client.post(
        "/v1/contacts", json={"identities": [{"channel": "sms", "address": "+15552224444"}]}
    ).json()
    conv = client.post("/v1/conversations", json={"contact_id": contact["id"]}).json()["id"]
    sent = client.post(f"/v1/conversations/{conv}/messages", json={"body": "did this land?"})
    sid = sent.json()["channel_meta"]["provider_message_id"]
    assert sent.json()["delivery_status"] == "sent"

    resp = client.post(
        "/webhooks/twilio/sms/status", data={"MessageSid": sid, "MessageStatus": "delivered"}
    )
    assert resp.status_code == 200
    transcript = client.get(f"/v1/conversations/{conv}/transcript").json()
    assert transcript[0]["delivery_status"] == "delivered"


def test_failed_delivery_logs_event(client: TestClient) -> None:
    contact = client.post(
        "/v1/contacts", json={"identities": [{"channel": "sms", "address": "+15553335555"}]}
    ).json()
    conv = client.post("/v1/conversations", json={"contact_id": contact["id"]}).json()["id"]
    sent = client.post(f"/v1/conversations/{conv}/messages", json={"body": "risky send"})
    sid = sent.json()["channel_meta"]["provider_message_id"]

    client.post(
        "/webhooks/twilio/sms/status", data={"MessageSid": sid, "MessageStatus": "failed"}
    )
    transcript = client.get(f"/v1/conversations/{conv}/transcript").json()
    assert transcript[0]["delivery_status"] == "failed"
    events = client.get(f"/v1/conversations/{conv}/events").json()
    assert any(
        e["type"] == "note" and e["payload"].get("note") == "delivery failed" for e in events
    )


def test_unknown_sid_is_ignored(client: TestClient) -> None:
    resp = client.post(
        "/webhooks/twilio/sms/status",
        data={"MessageSid": "SM-does-not-exist", "MessageStatus": "delivered"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "unknown message"
