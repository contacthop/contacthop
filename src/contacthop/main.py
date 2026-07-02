"""FastAPI application factory and adapter wiring."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from contacthop import __version__
from contacthop.api.deps import require_api_key
from contacthop.api.routes import contacts, conversations
from contacthop.api.webhooks import email_inbound, twilio_sms, twilio_voice
from contacthop.channels.base import ChannelAdapter
from contacthop.channels.email.console import ConsoleEmailAdapter
from contacthop.channels.email.smtp import SMTPEmailAdapter
from contacthop.channels.sms.console import ConsoleSMSAdapter
from contacthop.channels.sms.twilio import TwilioSMSAdapter
from contacthop.channels.voice.console import ConsoleVoiceAdapter
from contacthop.channels.voice.twilio_call import TwilioVoiceAdapter
from contacthop.config import Settings
from contacthop.db.session import Database
from contacthop.domain.enums import ChannelType
from contacthop.orchestrator.scheduler import FollowUpScheduler

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
        status_callback = (
            f"{settings.public_base_url}/webhooks/twilio/sms/status"
            if settings.public_base_url
            else None
        )
        adapters[ChannelType.SMS] = TwilioSMSAdapter(
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            settings.twilio_from_number,
            status_callback_url=status_callback,
        )
    else:
        adapters[ChannelType.SMS] = ConsoleSMSAdapter()

    if settings.email_adapter == "smtp":
        if not (settings.smtp_host and settings.smtp_from_address):
            raise ValueError(
                "email_adapter='smtp' requires CONTACTHOP_SMTP_HOST and "
                "CONTACTHOP_SMTP_FROM_ADDRESS"
            )
        adapters[ChannelType.EMAIL] = SMTPEmailAdapter(
            host=settings.smtp_host,
            port=settings.smtp_port,
            from_address=settings.smtp_from_address,
            username=settings.smtp_username,
            password=settings.smtp_password,
            starttls=settings.smtp_starttls,
        )
    elif settings.email_adapter == "console":
        adapters[ChannelType.EMAIL] = ConsoleEmailAdapter()

    if settings.voice_adapter == "twilio":
        if not (
            settings.twilio_account_sid
            and settings.twilio_auth_token
            and settings.twilio_from_number
            and settings.public_base_url
        ):
            raise ValueError(
                "voice_adapter='twilio' requires CONTACTHOP_TWILIO_ACCOUNT_SID, "
                "CONTACTHOP_TWILIO_AUTH_TOKEN, CONTACTHOP_TWILIO_FROM_NUMBER, and "
                "CONTACTHOP_PUBLIC_BASE_URL (Twilio must reach the voice webhooks)"
            )
        adapters[ChannelType.VOICE] = TwilioVoiceAdapter(
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            settings.twilio_from_number,
        )
    elif settings.voice_adapter == "console":
        adapters[ChannelType.VOICE] = ConsoleVoiceAdapter()
    return adapters


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.database_url)
    adapters = build_adapters(settings)
    scheduler = FollowUpScheduler(db, settings, set(adapters))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await db.create_all()
        await scheduler.start()
        logger.info(
            "ContactHop %s ready (sms: %s, email: %s)",
            __version__,
            settings.sms_adapter,
            settings.email_adapter,
        )
        yield
        await scheduler.stop()
        await db.dispose()

    app = FastAPI(title="ContactHop", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.adapters = adapters
    app.state.scheduler = scheduler

    protected = [Depends(require_api_key)]
    app.include_router(contacts.router, dependencies=protected)
    app.include_router(conversations.router, dependencies=protected)
    app.include_router(twilio_sms.router)
    app.include_router(twilio_voice.router)
    app.include_router(email_inbound.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app
