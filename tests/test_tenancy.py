"""Multi-tenancy: agent keys see only their own tenant's data; admin sees all;
each tenant's events go to its own webhook."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import contacthop.orchestrator.notifier as notifier
from contacthop.config import Settings
from contacthop.main import create_app

ADMIN = {"Authorization": "Bearer admin-root-key"}


@pytest.fixture()
def mt(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mt.db'}",
        api_keys="admin-root-key",
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        yield client


def make_tenant(mt: TestClient, name: str, webhook: str | None = None) -> dict:
    resp = mt.post("/v1/agents", json={"name": name, "webhook_url": webhook}, headers=ADMIN)
    assert resp.status_code == 201, resp.text
    return resp.json()


def auth(agent: dict) -> dict:
    return {"Authorization": f"Bearer {agent['api_key']}"}


def seed(mt: TestClient, agent: dict, phone: str) -> tuple[str, str]:
    contact = mt.post(
        "/v1/contacts",
        json={"identities": [{"channel": "sms", "address": phone}]},
        headers=auth(agent),
    ).json()
    conversation = mt.post(
        "/v1/conversations", json={"contact_id": contact["id"]}, headers=auth(agent)
    ).json()
    return contact["id"], conversation["id"]


def test_agent_creation_and_key_shown_once(mt: TestClient) -> None:
    alpha = make_tenant(mt, "alpha")
    assert alpha["api_key"].startswith("chk_")
    listed = mt.get("/v1/agents", headers=ADMIN).json()
    assert [a["name"] for a in listed] == ["alpha"]
    assert "api_key" not in listed[0]  # never shown again


def test_agent_keys_are_tenant_scoped(mt: TestClient) -> None:
    alpha, beta = make_tenant(mt, "alpha"), make_tenant(mt, "beta")
    a_contact, a_conv = seed(mt, alpha, "+15551110001")
    seed(mt, beta, "+15551110002")

    # each tenant lists only its own world
    assert len(mt.get("/v1/contacts", headers=auth(alpha)).json()) == 1
    assert len(mt.get("/v1/conversations", headers=auth(beta)).json()) == 1

    # cross-tenant access is indistinguishable from nonexistent
    assert mt.get(f"/v1/contacts/{a_contact}", headers=auth(beta)).status_code == 404
    assert (
        mt.get(f"/v1/conversations/{a_conv}/transcript", headers=auth(beta)).status_code == 404
    )
    assert (
        mt.post(
            f"/v1/conversations/{a_conv}/messages",
            json={"body": "hi"},
            headers=auth(beta),
        ).status_code
        == 404
    )
    assert (
        mt.post(
            f"/v1/contacts/{a_contact}/memory", json={"text": "x"}, headers=auth(beta)
        ).status_code
        == 404
    )

    # admin sees everything
    assert len(mt.get("/v1/contacts", headers=ADMIN).json()) == 2
    assert mt.get(f"/v1/contacts/{a_contact}", headers=ADMIN).status_code == 200


def test_agent_keys_cannot_manage_agents(mt: TestClient) -> None:
    alpha = make_tenant(mt, "alpha")
    assert mt.get("/v1/agents", headers=auth(alpha)).status_code == 403
    assert mt.post("/v1/agents", json={"name": "sneaky"}, headers=auth(alpha)).status_code == 403


def test_bogus_and_missing_keys_rejected(mt: TestClient) -> None:
    assert mt.get("/v1/contacts").status_code == 401
    assert (
        mt.get("/v1/contacts", headers={"Authorization": "Bearer chk_bogus"}).status_code == 401
    )


def test_key_rotation_invalidates_old_key(mt: TestClient) -> None:
    alpha = make_tenant(mt, "alpha")
    rotated = mt.post(f"/v1/agents/{alpha['id']}/rotate-key", headers=ADMIN).json()
    assert rotated["api_key"] != alpha["api_key"]
    assert mt.get("/v1/contacts", headers=auth(alpha)).status_code == 401
    assert mt.get("/v1/contacts", headers=auth(rotated)).status_code == 200


def test_per_agent_webhook_routing(
    mt: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    posts: list[tuple[str, str]] = []

    async def fake_post(url: str, payload: dict) -> None:
        posts.append((url, payload["event"]))

    monkeypatch.setattr(notifier, "_post", fake_post)

    alpha = make_tenant(mt, "alpha", webhook="http://alpha.agent/hook")
    beta = make_tenant(mt, "beta", webhook="http://beta.agent/hook")
    seed(mt, alpha, "+15551110001")
    seed(mt, beta, "+15551110002")

    # inbound SMS from each tenant's contact routes to that tenant's webhook
    for phone in ("+15551110001", "+15551110002"):
        mt.post(
            "/webhooks/twilio/sms",
            data={"From": phone, "To": "+15550000000", "Body": "hello"},
        )
    assert ("http://alpha.agent/hook", "conversation.message.received") in posts
    assert ("http://beta.agent/hook", "conversation.message.received") in posts

    # each tenant sees only its own deliveries
    assert len(mt.get("/v1/deliveries", headers=auth(alpha)).json()) == 1
    assert len(mt.get("/v1/deliveries", headers=auth(beta)).json()) == 1
    assert len(mt.get("/v1/deliveries", headers=ADMIN).json()) == 2


def test_open_dev_mode_still_works(client: TestClient) -> None:
    # no keys configured anywhere → full access, agent_id=None everywhere
    contact = client.post(
        "/v1/contacts", json={"identities": [{"channel": "sms", "address": "+15559990001"}]}
    )
    assert contact.status_code == 201
    agents = client.get("/v1/agents")
    assert agents.status_code == 200  # open mode is admin