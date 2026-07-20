"""Contact memory API: agents decide what to remember; the store keeps it."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query

from contacthop.api.deps import MemoryDep, Principal, PrincipalDep, SessionDep, ensure_visible
from contacthop.domain.models import Contact
from contacthop.domain.schemas import ContactMemoryFact, MemoryFact, MemoryFactCreate
from contacthop.memory.store import MemoryDisabledError

router = APIRouter(prefix="/v1", tags=["memory"])


async def _require_contact(
    session: SessionDep, contact_id: uuid.UUID, principal: Principal
) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    ensure_visible(contact.agent_id, principal)
    return contact


@router.post("/contacts/{contact_id}/memory", response_model=MemoryFact, status_code=201)
async def remember(
    contact_id: uuid.UUID,
    payload: MemoryFactCreate,
    session: SessionDep,
    memory: MemoryDep,
    principal: PrincipalDep,
) -> MemoryFact:
    await _require_contact(session, contact_id, principal)
    try:
        return await memory.remember(contact_id, payload)
    except MemoryDisabledError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/contacts/{contact_id}/memory", response_model=list[MemoryFact])
async def recall(
    contact_id: uuid.UUID,
    session: SessionDep,
    memory: MemoryDep,
    principal: PrincipalDep,
    topic: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[MemoryFact]:
    await _require_contact(session, contact_id, principal)
    return await memory.recall(contact_id, topic=topic, limit=limit)


@router.get("/contacts/{contact_id}/memory/topics", response_model=list[str])
async def topics(
    contact_id: uuid.UUID, session: SessionDep, memory: MemoryDep, principal: PrincipalDep
) -> list[str]:
    await _require_contact(session, contact_id, principal)
    return await memory.topics(contact_id)


@router.delete("/contacts/{contact_id}/memory/{fact_id}", status_code=204)
async def forget(
    contact_id: uuid.UUID,
    fact_id: uuid.UUID,
    session: SessionDep,
    memory: MemoryDep,
    principal: PrincipalDep,
) -> None:
    await _require_contact(session, contact_id, principal)
    if not await memory.forget(contact_id, fact_id):
        raise HTTPException(status_code=404, detail="fact not found")


@router.get("/memory/topics/{topic}", response_model=list[ContactMemoryFact])
async def recall_topic(
    topic: str, memory: MemoryDep, limit: int = Query(default=100, ge=1, le=500)
) -> list[ContactMemoryFact]:
    """Cross-contact recall: every remembered fact about a topic — the graph payoff."""
    return await memory.recall_topic(topic, limit=limit)
