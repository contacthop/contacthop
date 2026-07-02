"""Application settings, driven by environment variables (prefix ``CONTACTHOP_``)."""

from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from contacthop.orchestrator.windows import parse_window


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONTACTHOP_", env_file=".env", extra="ignore"
    )

    database_url: str = "sqlite+aiosqlite:///contacthop.db"

    # "console" logs outbound messages instead of sending them — zero-credential dev mode.
    sms_adapter: Literal["console", "twilio"] = "console"
    email_adapter: Literal["none", "console", "smtp"] = "console"
    voice_adapter: Literal["none", "console", "twilio"] = "console"

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

    # Comma-separated Bearer tokens required on the /v1 management API.
    # Unset = open (dev mode). Webhooks use their own verification instead.
    api_keys: str | None = None

    # Per-contact outbound cap across all channels, rolling hour. 0 = unlimited.
    # Contacts can override via preferences["max_messages_per_hour"].
    max_messages_per_hour: int = 30

    # How often the in-process scheduler checks for due follow-ups.
    follow_up_poll_interval: float = 5.0

    # Send windows (quiet hours), per channel: "HH:MM-HH:MM" in the contact's
    # timezone; wraps midnight if start > end; unset = always allowed. Contacts
    # can override per channel via preferences["send_windows"].
    send_window_sms: str | None = None
    send_window_email: str | None = None
    send_window_voice: str | None = None
    # Timezone the windows are evaluated in when a contact has no
    # preferences["timezone"] of their own (IANA name, e.g. America/Chicago).
    default_timezone: str = "UTC"

    @field_validator("send_window_sms", "send_window_email", "send_window_voice")
    @classmethod
    def _valid_window(cls, value: str | None) -> str | None:
        if value and value.strip().lower() not in {"always", "any", "24/7"}:
            parse_window(value)  # fail fast at startup on malformed specs
        return value

    @field_validator("default_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:  # ZoneInfoNotFoundError subclasses KeyError
            raise ValueError(f"unknown IANA timezone {value!r}") from exc
        return value
