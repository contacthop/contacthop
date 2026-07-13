"""Agent (tenant) management: admin-only.

An agent is an isolated tenant: its API key sees only its own contacts,
conversations, memory, and webhook deliveries, and its events are pushed to
its own webhook URL. Keys are stored hashed and shown exactly once.
"""

from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from contacthop.api.deps import PrincipalDep, SessionDep, hash_key, require_admin
from contacthop.domain.models import Agent
from contacthop.domain.schemas import (
    AgentCreate,
    AgentCreatedRead,
    AgentRead,
    AgentUpdate,
)

router = APIRouter(prefix="/v1/agents", tags=["agents"])


def _new_key() -> str:
    return "chk_" + secrets.token_hex(24)


def _created(agent: Agent, api_key: str) -> AgentCreatedRead:
    return AgentCreatedRead(
        id=agent.id,
        name=agent.name,
        webhook_url=agent.webhook_url,
        created_at=agent.created_at,
        api_key=api_key,
    )


@router.post("", response_model=AgentCreatedRead, status_code=201)
async def create_agent(
    payload: AgentCreate, session: SessionDep, principal: PrincipalDep
) -> AgentCreatedRead:
    """Create a tenant. The response carries the API key — store it now,
    it is never shown again."""
    require_admin(principal)
    existing = await session.execute(select(Agent).where(Agent.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="agent name already exists")

    api_key = _new_key()
    agent = Agent(name=payload.name, webhook_url=payload.webhook_url, key_hash=hash_key(api_key))
    session.add(agent)
    await session.flush()
    return _created(agent, api_key)


@router.get("", response_model=list[AgentRead])
async def list_agents(session: SessionDep, principal: PrincipalDep) -> list[Agent]:
    require_admin(principal)
    result = await session.execute(select(Agent).order_by(Agent.created_at, Agent.id))
    return list(result.scalars())


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(
    agent_id: uuid.UUID, payload: AgentUpdate, session: SessionDep, principal: PrincipalDep
) -> Agent:
    require_admin(principal)
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    if payload.name is not None:
        agent.name = payload.name
    if payload.webhook_url is not None:
        agent.webhook_url = payload.webhook_url
    await session.flush()
    return agent


@router.post("/{agent_id}/rotate-key", response_model=AgentCreatedRead)
async def rotate_key(
    agent_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> AgentCreatedRead:
    """Invalidate the agent's key and mint a new one (shown once)."""
    require_admin(principal)
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    api_key = _new_key()
    agent.key_hash = hash_key(api_key)
    await session.flush()
    return _created(agent, api_key)
