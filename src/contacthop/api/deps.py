from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from contacthop.channels.base import ChannelAdapter
from contacthop.config import Settings
from contacthop.domain.enums import ChannelType


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.session() as session:
        yield session
        await session.commit()


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_adapters(request: Request) -> dict[ChannelType, ChannelAdapter]:
    adapters: dict[ChannelType, ChannelAdapter] = request.app.state.adapters
    return adapters


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
