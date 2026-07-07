"""Memory API and store backends (in-memory + disabled; FalkorDB has its own
integration suite in test_falkordb.py, run in CI against a real instance)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contacthop.config import Settings
from contacthop.main import create_app


def make_contact(client: TestClient, name: str = "Ada") -> str:
    resp = client.post(
        "/v1/contacts",
        json={"display_name": name, "identities": [{"channel": "sms", "address": "+15551230000"}]},
    )
    return resp.json()["id"]


def test_remember_recall_topics_forget(client: TestClient) -> None:
    contact = make_contact(client)

    first = client.post(
        f"/v1/contacts/{contact}/memory",
        json={"text": "prefers 4pm meetings", "topic": "scheduling"},
    )
    assert first.status_code == 201, first.text
    fact = first.json()
    assert fact["text"] == "prefers 4pm meetings"
    client.post(
        f"/v1/contacts/{contact}/memory",
        json={"text": "wants agendas by email", "topic": "scheduling"},
    )
    client.post(f"/v1/contacts/{contact}/memory", json={"text": "has a dog named Byte"})

    everything = client.get(f"/v1/contacts/{contact}/memory").json()
    assert len(everything) == 3
    scheduling = client.get(f"/v1/contacts/{contact}/memory?topic=scheduling").json()
    assert len(scheduling) == 2
    assert client.get(f"/v1/contacts/{contact}/memory/topics").json() == ["scheduling"]

    assert client.delete(f"/v1/contacts/{contact}/memory/{fact['id']}").status_code == 204
    assert len(client.get(f"/v1/contacts/{contact}/memory").json()) == 2
    assert client.delete(f"/v1/contacts/{contact}/memory/{fact['id']}").status_code == 404


def test_cross_contact_topic_recall(client: TestClient) -> None:
    ada = make_contact(client, "Ada")
    grace = client.post(
        "/v1/contacts",
        json={
            "display_name": "Grace",
            "identities": [{"channel": "sms", "address": "+15551239999"}],
        },
    ).json()["id"]

    client.post(
        f"/v1/contacts/{ada}/memory", json={"text": "asked about pricing", "topic": "pricing"}
    )
    client.post(
        f"/v1/contacts/{grace}/memory",
        json={"text": "negotiating a discount", "topic": "pricing"},
    )

    rows = client.get("/v1/memory/topics/pricing").json()
    assert len(rows) == 2
    assert {r["contact_id"] for r in rows} == {ada, grace}


def test_context_includes_memory(client: TestClient) -> None:
    contact = make_contact(client)
    conversation = client.post("/v1/conversations", json={"contact_id": contact}).json()
    client.post(
        f"/v1/contacts/{contact}/memory",
        json={"text": "prefers 4pm meetings", "conversation_id": conversation["id"]},
    )

    ctx = client.get(f"/v1/conversations/{conversation['id']}/context").json()
    assert [f["text"] for f in ctx["memory"]] == ["prefers 4pm meetings"]
    assert ctx["memory"][0]["conversation_id"] == conversation["id"]


def test_memory_for_unknown_contact_404(client: TestClient) -> None:
    resp = client.post(
        "/v1/contacts/00000000-0000-0000-0000-000000000000/memory", json={"text": "x"}
    )
    assert resp.status_code == 404


@pytest.fixture()
def no_memory_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'nomem.db'}",
        memory_store="none",
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_disabled_store_rejects_writes_but_reads_are_empty(
    no_memory_client: TestClient,
) -> None:
    contact = make_contact(no_memory_client)
    write = no_memory_client.post(f"/v1/contacts/{contact}/memory", json={"text": "x"})
    assert write.status_code == 422
    assert "no memory store configured" in write.json()["detail"]

    assert no_memory_client.get(f"/v1/contacts/{contact}/memory").json() == []
    conversation = no_memory_client.post("/v1/conversations", json={"contact_id": contact}).json()
    ctx = no_memory_client.get(f"/v1/conversations/{conversation['id']}/context").json()
    assert ctx["memory"] == []
