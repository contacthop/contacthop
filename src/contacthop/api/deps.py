from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
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


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
AdaptersDep = Annotated[dict[ChannelType, ChannelAdapter], Depends(get_adapters)]
