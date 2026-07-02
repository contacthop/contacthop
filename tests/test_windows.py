"""Send windows (quiet hours): parsing, wrap-around, and gateway enforcement."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contacthop.config import Settings
from contacthop.domain.enums import ChannelType
from contacthop.domain.models import ChannelSession
from contacthop.main import create_app
from contacthop.orchestrator.windows import window_open

# ── pure window logic ────────────────────────────────────────────────────────


def test_window_open_plain_range() -> None:
    assert window_open("08:00-17:00", time(8, 0))
    assert window_open("08:00-17:00", time(12, 30))
    assert not window_open("08:00-17:00", time(17, 0))  # end is exclusive
    assert not window_open("08:00-17:00", time(3, 0))


def test_window_wraps_midnight() -> None:
    assert window_open("21:00-08:00", time(23, 0))
    assert window_open("21:00-08:00", time(2, 0))
    assert not window_open("21:00-08:00", time(12, 0))


def test_window_always_and_never() -> None:
    assert window_open(None, time(3, 0))
    assert window_open("always", time(3, 0))
    assert not window_open("10:00-10:00", time(10, 0))  # zero-length: never open


def test_malformed_window_rejected_at_startup() -> None:
    with pytest.raises(ValueError):
        Settings(send_window_sms="8am to 5pm", _env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(default_timezone="Mars/Olympus", _env_file=None)  # type: ignore[call-arg]


# ── gateway enforcement ──────────────────────────────────────────────────────


def _window(offset_start_min: int, offset_end_min: int) -> str:
    """A window positioned relative to the current UTC time (deterministic per run)."""
    now = datetime.now(UTC)
    start = (now + timedelta(minutes=offset_start_min)).strftime("%H:%M")
    end = (now + timedelta(minutes=offset_end_min)).strftime("%H:%M")
    return f"{start}-{end}"

OPEN_NOW = _window(-60, 60)
CLOSED_NOW = _window(60, 120)


@pytest.fixture()
def quiet_client(tmp_path: Path) -> Iterator[TestClient]:
    """SMS and voice closed right now; email open."""
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'quiet.db'}",
        send_window_sms=CLOSED_NOW,
        send_window_email=OPEN_NOW,
        send_window_voice=CLOSED_NOW,
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _setup(client: TestClient, identities: list[dict]) -> str:
    contact = client.post("/v1/contacts", json={"identities": identities}).json()
    conversation = client.post(
        "/v1/conversations", json={"contact_id": contact["id"], "channel": "sms"}
    ).json()
    return conversation["id"]


def test_explicit_send_on_closed_channel_rejected(quiet_client: TestClient) -> None:
    conv = _setup(quiet_client, [{"channel": "sms", "address": "+15550101010"}])
    resp = quiet_client.post(
        f"/v1/conversations/{conv}/messages", json={"body": "hi", "channel": "sms"}
    )
    assert resp.status_code == 422
    assert "outside its send window" in resp.json()["detail"]


def test_policy_reroutes_to_open_channel(quiet_client: TestClient) -> None:
    conv = _setup(
        quiet_client,
        [
            {"channel": "sms", "address": "+15550101010"},
            {"channel": "email", "address": "night@example.com"},
        ],
    )
    resp = quiet_client.post(f"/v1/conversations/{conv}/messages", json={"body": "hi"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["channel"] == "email"


def test_all_channels_closed_rejected(quiet_client: TestClient) -> None:
    conv = _setup(quiet_client, [{"channel": "sms", "address": "+15550101010"}])
    resp = quiet_client.post(f"/v1/conversations/{conv}/messages", json={"body": "hi"})
    assert resp.status_code == 422
    assert "send windows" in resp.json()["detail"]


def test_call_blocked_outside_voice_window(quiet_client: TestClient) -> None:
    conv = _setup(quiet_client, [{"channel": "sms", "address": "+15550101010"}])
    resp = quiet_client.post(f"/v1/conversations/{conv}/call", json={"body": "hello?"})
    assert resp.status_code == 422
    assert "voice is outside its send window" in resp.json()["detail"]


def test_live_call_exempt_from_quiet_hours(quiet_client: TestClient) -> None:
    conv = _setup(quiet_client, [{"channel": "sms", "address": "+15550101010"}])

    async def _force_open_call() -> None:
        db = quiet_client.app.state.db
        async with db.session() as session:
            session.add(
                ChannelSession(
                    conversation_id=uuid.UUID(conv),
                    channel=ChannelType.VOICE,
                    session_meta={"call_sid": "CA-live"},
                )
            )
            await session.commit()

    quiet_client.portal.call(_force_open_call)

    resp = quiet_client.post(f"/v1/conversations/{conv}/messages", json={"body": "still there?"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["channel"] == "voice"
    assert resp.json()["delivery_status"] == "queued"


def test_contact_override_beats_global_window(quiet_client: TestClient) -> None:
    # global sms window is closed, but this contact allows sms around the clock
    contact = quiet_client.post(
        "/v1/contacts",
        json={
            "identities": [{"channel": "sms", "address": "+15550202020"}],
            "preferences": {"send_windows": {"sms": "always"}},
        },
    ).json()
    conversation = quiet_client.post(
        "/v1/conversations", json={"contact_id": contact["id"], "channel": "sms"}
    ).json()
    resp = quiet_client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"body": "hi", "channel": "sms"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["channel"] == "sms"
