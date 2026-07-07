from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from contacthop.channels.base import ChannelAdapter
from contacthop.config import Settings
from contacthop.domain.enums import ChannelType
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


def require_api_key(request: Request) -> None:
    """Bearer-token auth for the management API; no-op when no keys configured."""
    settings: Settings = request.app.state.settings
    if not settings.api_keys:
        return
    keys = {k.strip() for k in settings.api_keys.split(",") if k.strip()}
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    if not any(hmac.compare_digest(token, key) for key in keys):
        raise HTTPException(
            status_code=401,
            detail="invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
AdaptersDep = Annotated[dict[ChannelType, ChannelAdapter], Depends(get_adapters)]
MemoryDep = Annotated[MemoryStore, Depends(get_memory)]
