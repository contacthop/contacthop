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
    email_adapter: Literal["none", "console", "smtp"] = "console"

    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    # Public base URL of this deployment, needed for Twilio webhook signature validation.
    public_base_url: str | None = None

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_address: str | None = None
    smtp_starttls: bool = True

    # Shared secret required (as X-ContactHop-Token) on the generic inbound email webhook.
    email_inbound_token: str | None = None

    # Where inbound conversation events are pushed for the agent runtime.
    agent_webhook_url: str | None = None

    # How often the in-process scheduler checks for due follow-ups.
    follow_up_poll_interval: float = 5.0
