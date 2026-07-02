"""Application settings, driven by environment variables (prefix ``CONTACTHOP_``)."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONTACTHOP_", env_file=".env", extra="ignore"
    )

    database_url: str = "sqlite+aiosqlite:///contacthop.db"

    # "console" logs outbound messages instead of sending them — zero-credential dev mode.
    sms_adapter: Literal["console", "twilio"] = "console"

    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    # Public base URL of this deployment, needed for Twilio webhook signature validation.
    public_base_url: str | None = None

    # Where inbound conversation events are pushed for the agent runtime.
    agent_webhook_url: str | None = None
