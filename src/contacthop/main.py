"""FastAPI application factory and adapter wiring."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from contacthop import __version__
from contacthop.api.routes import contacts, conversations
from contacthop.api.webhooks import twilio_sms
from contacthop.channels.base import ChannelAdapter
from contacthop.channels.sms.console import ConsoleSMSAdapter
from contacthop.channels.sms.twilio import TwilioSMSAdapter
from contacthop.config import Settings
from contacthop.db.session import Database
from contacthop.domain.enums import ChannelType

logger = logging.getLogger("contacthop")


def build_adapters(settings: Settings) -> dict[ChannelType, ChannelAdapter]:
    adapters: dict[ChannelType, ChannelAdapter] = {}
    if settings.sms_adapter == "twilio":
        if not (
            settings.twilio_account_sid
            and settings.twilio_auth_token
            and settings.twilio_from_number
        ):
            raise ValueError(
                "sms_adapter='twilio' requires CONTACTHOP_TWILIO_ACCOUNT_SID, "
                "CONTACTHOP_TWILIO_AUTH_TOKEN, and CONTACTHOP_TWILIO_FROM_NUMBER"
            )
        adapters[ChannelType.SMS] = TwilioSMSAdapter(
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            settings.twilio_from_number,
        )
    else:
        adapters[ChannelType.SMS] = ConsoleSMSAdapter()
    return adapters


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await db.create_all()
        logger.info("ContactHop %s ready (sms adapter: %s)", __version__, settings.sms_adapter)
        yield
        await db.dispose()

    app = FastAPI(title="ContactHop", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.adapters = build_adapters(settings)

    app.include_router(contacts.router)
    app.include_router(conversations.router)
    app.include_router(twilio_sms.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app
