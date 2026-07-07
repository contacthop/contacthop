"""Regression: a resource must be usable immediately after its 201.

The original yield-dependency session committed after the response was sent,
so a client could create a contact and 404 when referencing it in the very
next request. The db-session middleware commits before the response goes out.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_then_use_immediately(client: TestClient) -> None:
    for i in range(20):
        contact = client.post(
            "/v1/contacts",
            json={"identities": [{"channel": "sms", "address": f"+1555100{i:04d}"}]},
        )
        assert contact.status_code == 201
        conversation = client.post(
            "/v1/conversations", json={"contact_id": contact.json()["id"]}
        )
        assert conversation.status_code == 201, conversation.text
        sent = client.post(
            f"/v1/conversations/{conversation.json()['id']}/messages",
            json={"body": "hello right away"},
        )
        assert sent.status_code == 201, sent.text


def test_error_responses_roll_back(client: TestClient) -> None:
    # 404 mid-request must not leave partial writes behind
    resp = client.post(
        "/v1/conversations",
        json={"contact_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 404
    assert client.get("/v1/conversations").json() == []
