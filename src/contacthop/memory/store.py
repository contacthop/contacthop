"""Durable contact memory behind a small protocol.

Backends:
- ``none``      — memory disabled; recall is empty, remember is rejected.
- ``inmemory``  — process-local dict, zero-config dev/demo (lost on restart).
- ``falkordb``  — a knowledge graph: (Contact)-[:KNOWS]->(Fact)-[:ABOUT]->(Topic),
                  with (Fact)-[:FROM]->(Conversation) provenance. Graph shape is
                  what buys cross-contact topic traversal and future relationship
                  queries; requires the ``contacthop[falkordb]`` extra.

The harness never decides *what* to remember — agents do, via the memory API.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from contacthop.domain.schemas import ContactMemoryFact, MemoryFact, MemoryFactCreate


class MemoryDisabledError(RuntimeError):
    """Raised by write operations when no memory store is configured."""


class MemoryStore(Protocol):
    enabled: bool

    async def remember(self, contact_id: uuid.UUID, fact: MemoryFactCreate) -> MemoryFact: ...

    async def recall(
        self, contact_id: uuid.UUID, topic: str | None = None, limit: int = 50
    ) -> list[MemoryFact]: ...

    async def topics(self, contact_id: uuid.UUID) -> list[str]: ...

    async def forget(self, contact_id: uuid.UUID, fact_id: uuid.UUID) -> bool: ...

    async def recall_topic(self, topic: str, limit: int = 100) -> list[ContactMemoryFact]: ...


def _new_fact(fact: MemoryFactCreate) -> MemoryFact:
    return MemoryFact(
        id=uuid.uuid4(),
        created_at=datetime.now(UTC),
        text=fact.text,
        topic=fact.topic,
        conversation_id=fact.conversation_id,
    )


class DisabledMemoryStore:
    enabled = False

    async def remember(self, contact_id: uuid.UUID, fact: MemoryFactCreate) -> MemoryFact:
        raise MemoryDisabledError(
            "no memory store configured; set CONTACTHOP_MEMORY_STORE=inmemory or falkordb"
        )

    async def recall(
        self, contact_id: uuid.UUID, topic: str | None = None, limit: int = 50
    ) -> list[MemoryFact]:
        return []

    async def topics(self, contact_id: uuid.UUID) -> list[str]:
        return []

    async def forget(self, contact_id: uuid.UUID, fact_id: uuid.UUID) -> bool:
        return False

    async def recall_topic(self, topic: str, limit: int = 100) -> list[ContactMemoryFact]:
        return []


class InMemoryMemoryStore:
    """Dev/demo backend: fully functional, process-local, not persistent."""

    enabled = True

    def __init__(self) -> None:
        self._facts: dict[uuid.UUID, list[MemoryFact]] = {}

    async def remember(self, contact_id: uuid.UUID, fact: MemoryFactCreate) -> MemoryFact:
        stored = _new_fact(fact)
        self._facts.setdefault(contact_id, []).append(stored)
        return stored

    async def recall(
        self, contact_id: uuid.UUID, topic: str | None = None, limit: int = 50
    ) -> list[MemoryFact]:
        facts = self._facts.get(contact_id, [])
        if topic is not None:
            facts = [f for f in facts if f.topic == topic]
        return sorted(facts, key=lambda f: f.created_at, reverse=True)[:limit]

    async def topics(self, contact_id: uuid.UUID) -> list[str]:
        return sorted({f.topic for f in self._facts.get(contact_id, []) if f.topic})

    async def forget(self, contact_id: uuid.UUID, fact_id: uuid.UUID) -> bool:
        facts = self._facts.get(contact_id, [])
        remaining = [f for f in facts if f.id != fact_id]
        self._facts[contact_id] = remaining
        return len(remaining) < len(facts)

    async def recall_topic(self, topic: str, limit: int = 100) -> list[ContactMemoryFact]:
        rows = [
            ContactMemoryFact(contact_id=contact_id, **fact.model_dump())
            for contact_id, facts in self._facts.items()
            for fact in facts
            if fact.topic == topic
        ]
        return sorted(rows, key=lambda f: f.created_at, reverse=True)[:limit]


class FalkorMemoryStore:
    """Knowledge-graph memory on FalkorDB (Cypher over the sync client, run in a
    worker thread — memory operations are low-QPS)."""

    enabled = True

    def __init__(
        self,
        host: str,
        port: int = 6379,
        username: str | None = None,
        password: str | None = None,
        graph_name: str = "contacthop",
    ) -> None:
        try:
            from falkordb import FalkorDB
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "memory_store='falkordb' requires the falkordb client: "
                "pip install 'contacthop[falkordb]'"
            ) from exc
        try:
            # The client connects eagerly, so a bad host fails here at startup —
            # deliberately fail fast with a pointer instead of a redis traceback.
            self._db = FalkorDB(host=host, port=port, username=username, password=password)
        except Exception as exc:
            raise RuntimeError(
                f"cannot reach FalkorDB at {host}:{port} — is it running? "
                "(docker compose up starts one; or set CONTACTHOP_MEMORY_STORE=inmemory)"
            ) from exc
        self._graph = self._db.select_graph(graph_name)

    async def _query(self, cypher: str, params: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._graph.query, cypher, params)

    async def remember(self, contact_id: uuid.UUID, fact: MemoryFactCreate) -> MemoryFact:
        stored = _new_fact(fact)
        cypher = (
            "MERGE (c:Contact {id: $contact_id}) "
            "CREATE (f:Fact {id: $fact_id, text: $text, topic: $topic, "
            "conversation_id: $conversation_id, created_at: $created_at}) "
            "CREATE (c)-[:KNOWS]->(f) "
        )
        if fact.topic:
            cypher += "MERGE (t:Topic {name: $topic}) CREATE (f)-[:ABOUT]->(t) "
        if fact.conversation_id:
            cypher += (
                "MERGE (v:Conversation {id: $conversation_id}) CREATE (f)-[:FROM]->(v) "
            )
        await self._query(
            cypher,
            {
                "contact_id": str(contact_id),
                "fact_id": str(stored.id),
                "text": stored.text,
                "topic": stored.topic or "",
                "conversation_id": str(stored.conversation_id or ""),
                "created_at": stored.created_at.isoformat(),
            },
        )
        return stored

    @staticmethod
    def _row_to_fact(row: list[Any]) -> MemoryFact:
        fact_id, text, topic, conversation_id, created_at = row
        return MemoryFact(
            id=uuid.UUID(fact_id),
            text=text,
            topic=topic or None,
            conversation_id=uuid.UUID(conversation_id) if conversation_id else None,
            created_at=datetime.fromisoformat(created_at),
        )

    async def recall(
        self, contact_id: uuid.UUID, topic: str | None = None, limit: int = 50
    ) -> list[MemoryFact]:
        cypher = (
            "MATCH (c:Contact {id: $contact_id})-[:KNOWS]->(f:Fact) "
            + ("WHERE f.topic = $topic " if topic is not None else "")
            + "RETURN f.id, f.text, f.topic, f.conversation_id, f.created_at "
            "ORDER BY f.created_at DESC LIMIT $limit"
        )
        result = await self._query(
            cypher, {"contact_id": str(contact_id), "topic": topic, "limit": limit}
        )
        return [self._row_to_fact(row) for row in result.result_set]

    async def topics(self, contact_id: uuid.UUID) -> list[str]:
        result = await self._query(
            "MATCH (c:Contact {id: $contact_id})-[:KNOWS]->(:Fact)-[:ABOUT]->(t:Topic) "
            "RETURN DISTINCT t.name ORDER BY t.name",
            {"contact_id": str(contact_id)},
        )
        return [row[0] for row in result.result_set]

    async def forget(self, contact_id: uuid.UUID, fact_id: uuid.UUID) -> bool:
        params = {"contact_id": str(contact_id), "fact_id": str(fact_id)}
        found = await self._query(
            "MATCH (c:Contact {id: $contact_id})-[:KNOWS]->(f:Fact {id: $fact_id}) "
            "RETURN f.id",
            params,
        )
        if not found.result_set:
            return False
        await self._query(
            "MATCH (c:Contact {id: $contact_id})-[:KNOWS]->(f:Fact {id: $fact_id}) "
            "DETACH DELETE f",
            params,
        )
        return True

    async def recall_topic(self, topic: str, limit: int = 100) -> list[ContactMemoryFact]:
        result = await self._query(
            "MATCH (c:Contact)-[:KNOWS]->(f:Fact)-[:ABOUT]->(t:Topic {name: $topic}) "
            "RETURN c.id, f.id, f.text, f.topic, f.conversation_id, f.created_at "
            "ORDER BY f.created_at DESC LIMIT $limit",
            {"topic": topic, "limit": limit},
        )
        return [
            ContactMemoryFact(
                contact_id=uuid.UUID(row[0]), **self._row_to_fact(row[1:]).model_dump()
            )
            for row in result.result_set
        ]
