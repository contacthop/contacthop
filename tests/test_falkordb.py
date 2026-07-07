"""FalkorDB integration tests — require a running FalkorDB instance.

Skipped unless CONTACTHOP_TEST_FALKORDB_HOST is set; CI runs them against a
falkordb/falkordb service container.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("falkordb")

FALKOR_HOST = os.environ.get("CONTACTHOP_TEST_FALKORDB_HOST")
pytestmark = pytest.mark.skipif(
    not FALKOR_HOST, reason="CONTACTHOP_TEST_FALKORDB_HOST not set"
)

from contacthop.domain.schemas import MemoryFactCreate  # noqa: E402
from contacthop.memory.store import FalkorMemoryStore  # noqa: E402


@pytest.fixture()
def store() -> FalkorMemoryStore:
    # A unique graph per test run keeps runs independent.
    return FalkorMemoryStore(
        host=FALKOR_HOST or "localhost",
        graph_name=f"contacthop_test_{uuid.uuid4().hex[:12]}",
    )


async def test_graph_roundtrip(store: FalkorMemoryStore) -> None:
    ada, grace = uuid.uuid4(), uuid.uuid4()
    conversation = uuid.uuid4()

    kept = await store.remember(
        ada,
        MemoryFactCreate(
            text="prefers 4pm meetings", topic="scheduling", conversation_id=conversation
        ),
    )
    await store.remember(ada, MemoryFactCreate(text="wants agendas by email", topic="scheduling"))
    await store.remember(ada, MemoryFactCreate(text="has a dog named Byte"))
    await store.remember(grace, MemoryFactCreate(text="books mornings only", topic="scheduling"))

    everything = await store.recall(ada)
    assert {f.text for f in everything} == {
        "prefers 4pm meetings",
        "wants agendas by email",
        "has a dog named Byte",
    }

    scheduling = await store.recall(ada, topic="scheduling")
    assert len(scheduling) == 2
    with_provenance = next(f for f in scheduling if f.text == "prefers 4pm meetings")
    assert with_provenance.conversation_id == conversation

    assert await store.topics(ada) == ["scheduling"]

    # the graph payoff: one topic traversed across contacts
    shared = await store.recall_topic("scheduling")
    assert {f.contact_id for f in shared} == {ada, grace}

    assert await store.forget(ada, kept.id) is True
    assert await store.forget(ada, kept.id) is False
    assert len(await store.recall(ada)) == 2

    # other contacts' facts are untouched
    assert len(await store.recall(grace)) == 1


async def test_recall_limit_and_ordering(store: FalkorMemoryStore) -> None:
    contact = uuid.uuid4()
    for i in range(5):
        await store.remember(contact, MemoryFactCreate(text=f"fact {i}"))
    limited = await store.recall(contact, limit=3)
    assert len(limited) == 3
    # newest first
    assert limited[0].created_at >= limited[-1].created_at
