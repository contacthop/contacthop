from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contacthop.channels.base import ChannelAdapter
from contacthop.config import Settings
from contacthop.domain.enums import ChannelType
from contacthop.domain.models import Agent
from contacthop.memory.store import MemoryStore


def get_session(request: Request) -> AsyncSession:
    """The request-scoped session opened by the db-session middleware.

    The middleware commits BEFORE the response is sent (a yield-dependency's
    teardown runs after it, which broke read-your-writes: a client could POST
    a contact, get 201, and immediately 404 using it).
    """
    session: AsyncSession = request.state.db_session
    return session


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_adapters(request: Request) -> dict[ChannelType, ChannelAdapter]:
    adapters: dict[ChannelType, ChannelAdapter] = request.app.state.adapters
    return adapters


def get_memory(request: Request) -> MemoryStore:
    memory: MemoryStore = request.app.state.memory
    return memory


@dataclass
class Principal:
    """Who is calling the management API.

    - admin: an env-configured key (CONTACTHOP_API_KEYS), or open dev mode
      with no keys anywhere — sees everything.
    - agent-scoped: a DB-backed agent key — sees only its own tenant's data.
    """

    agent: Agent | None = None
    is_admin: bool = False

    @property
    def agent_id(self) -> uuid.UUID | None:
        return self.agent.id if self.agent else None


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def authenticate(request: Request) -> Principal:
    settings: Settings = request.app.state.settings
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""

    admin_keys = {
        k.strip() for k in (settings.api_keys or "").split(",") if k.strip()
    }
    if token:
        if any(hmac.compare_digest(token, key) for key in admin_keys):
            return Principal(is_admin=True)
        session: AsyncSession = request.state.db_session
        result = await session.execute(select(Agent).where(Agent.key_hash == hash_key(token)))
        agent = result.scalar_one_or_none()
        if agent is not None:
            return Principal(agent=agent)
        raise HTTPException(
            status_code=401,
            detail="invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if admin_keys:
        raise HTTPException(
            status_code=401,
            detail="missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Open dev mode: no keys configured anywhere → full access.
    return Principal(is_admin=True)


def require_admin(principal: Principal) -> None:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin API key required")


def ensure_visible(owner_agent_id: uuid.UUID | None, principal: Principal) -> None:
    """404 when an agent-scoped key touches another tenant's row (indistinguishable
    from nonexistent — no cross-tenant existence oracle)."""
    if principal.agent is not None and owner_agent_id != principal.agent.id:
        raise HTTPException(status_code=404, detail="not found")


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
AdaptersDep = Annotated[dict[ChannelType, ChannelAdapter], Depends(get_adapters)]
MemoryDep = Annotated[MemoryStore, Depends(get_memory)]
PrincipalDep = Annotated[Principal, Depends(authenticate)]
