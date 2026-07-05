"""Dashboard shell and the list endpoints that power it."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_dashboard_served(client: TestClient) -> None:
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "ContactHop" in resp.text
    assert "command center" in resp.text


def test_list_contacts_and_conversations(client: TestClient) -> None:
    for i in range(3):
        contact = client.post(
            "/v1/contacts",
            json={
                "display_name": f"Contact {i}",
                "identities": [{"channel": "sms", "address": f"+1555000{i:04d}"}],
            },
        ).json()
        client.post(
            "/v1/conversations",
            json={"contact_id": contact["id"], "goal": f"goal {i}"},
        )

    contacts = client.get("/v1/contacts").json()
    assert len(contacts) == 3

    conversations = client.get("/v1/conversations").json()
    assert len(conversations) == 3
    # newest first
    assert conversations[0]["goal"] == "goal 2"

    active = client.get("/v1/conversations?status=active").json()
    assert len(active) == 3
    closed = client.get("/v1/conversations?status=closed").json()
    assert closed == []

    limited = client.get("/v1/contacts?limit=2").json()
    assert len(limited) == 2
