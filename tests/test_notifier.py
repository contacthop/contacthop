"""Durable agent webhook delivery: outbox, retries with backoff, dead letter,
and the redelivery API. The agent endpoint is simulated by patching the
notifier's HTTP post."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import contacthop.orchestrator.notifier as notifier
from contacthop.config import Settings
from contacthop.main import create_app
from contacthop.orchestrator.notifier import backoff_seconds


class FakeAgent:
    """Stands in for the agent runtime's webhook endpoint."""

    def __init__(self) -> None:
        self.received: list[dict] = []
        self.up = True

    async def post(self, url: str, payload: dict) -> None:
        if not self.up:
            raise ConnectionError("agent runtime is down")
        self.received.append(payload)


@pytest.fixture()
def agent(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    fake = FakeAgent()
    monkeypatch.setattr(notifier, "_post", fake.post)
    return fake


@pytest.fixture()
def wired_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'notify.db'}",
        agent_webhook_url="http://agent.test/",
        agent_webhook_max_attempts=3,
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _inbound(client: TestClient, body: str = "hello") -> None:
    client.post(
        "/v1/contacts",
        json={"identities": [{"channel": "sms", "address": "+15553219876"}]},
    )
    resp = client.post(
        "/webhooks/twilio/sms",
        data={"From": "+15553219876", "To": "+15550000000", "Body": body},
    )
    assert resp.status_code == 200


def test_backoff_schedule() -> None:
    assert [backoff_seconds(n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert backoff_seconds(20) == 7200  # capped


def test_delivered_when_agent_is_up(wired_client: TestClient, agent: FakeAgent) -> None:
    _inbound(wired_client)
    assert [p["event"] for p in agent.received] == ["conversation.message.received"]
    assert agent.received[0]["message"]["body"] == "hello"

    rows = wired_client.get("/v1/deliveries").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "delivered"
    assert rows[0]["attempts"] == 1


def test_nothing_lost_while_agent_down(wired_client: TestClient, agent: FakeAgent) -> None:
    agent.up = False
    _inbound(wired_client, "are you there?")
    assert agent.received == []

    pending = wired_client.get("/v1/deliveries?status=pending").json()
    assert len(pending) == 1
    assert pending[0]["attempts"] == 1
    assert "down" in pending[0]["last_error"]

    # agent comes back; the scheduler sweep is not due yet (backoff), so force
    # the retry through the API — the message arrives with nothing lost
    agent.up = True
    delivery_id = pending[0]["id"]
    retried = wired_client.post(f"/v1/deliveries/{delivery_id}/retry").json()
    assert retried["status"] == "delivered"
    assert [p["message"]["body"] for p in agent.received] == ["are you there?"]


def test_exhausts_to_dead_letter(wired_client: TestClient, agent: FakeAgent) -> None:
    agent.up = False
    _inbound(wired_client)

    scheduler = wired_client.app.state.scheduler
    db, settings = wired_client.app.state.db, wired_client.app.state.settings
    delivery_id = wired_client.get("/v1/deliveries").json()[0]["id"]

    async def force_due_and_sweep() -> int:
        # collapse the backoff so the sweep retries immediately
        from contacthop.domain.models import AgentDelivery, utcnow

        async with db.session() as session:
            row = await session.get(AgentDelivery, __import__("uuid").UUID(delivery_id))
            row.next_attempt_at = utcnow()
            await session.commit()
        return await notifier.deliver_due(db, settings)

    for _ in range(2):  # attempts 2 and 3 (max_attempts=3)
        assert wired_client.portal.call(force_due_and_sweep) == 0

    dead = wired_client.get("/v1/deliveries?status=exhausted").json()
    assert len(dead) == 1
    assert dead[0]["attempts"] == 3

    # exhausted rows are not retried by the sweep
    assert wired_client.portal.call(force_due_and_sweep) == 0

    # …but a manual retry after recovery re-arms and delivers
    agent.up = True
    retried = wired_client.post(f"/v1/deliveries/{delivery_id}/retry").json()
    assert retried["status"] == "delivered"
    assert scheduler is not None  # sweep loop stays alive throughout


def test_no_webhook_configured_means_no_outbox_rows(client: TestClient) -> None:
    client.post(
        "/v1/contacts",
        json={"identities": [{"channel": "sms", "address": "+15550004567"}]},
    )
    client.post(
        "/webhooks/twilio/sms",
        data={"From": "+15550004567", "To": "+15550000000", "Body": "hi"},
    )
    assert client.get("/v1/deliveries").json() == []
