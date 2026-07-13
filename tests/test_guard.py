"""Repetition guard: doom-looped model output never reaches a human."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from contacthop.config import Settings
from contacthop.main import create_app
from contacthop.outbound.guard import degenerate_reason

VARIED_LONG = " ".join(f"sentence {i} has its own unique content here" for i in range(30))


def test_looping_trigrams_detected() -> None:
    assert degenerate_reason("I will check on that. " * 30) is not None


def test_single_word_run_detected() -> None:
    text = "The answer is " + "wait " * 15 + "let me think about this properly now"
    reason = degenerate_reason(text)
    assert reason is not None and "'wait'" in reason


def test_char_run_detected_even_in_short_text() -> None:
    assert degenerate_reason("hah" + "a" * 100) is not None


def test_normal_text_passes() -> None:
    assert degenerate_reason(VARIED_LONG) is None
    assert degenerate_reason("Sounds good, see you at 4pm!") is None


def test_short_emphasis_is_allowed() -> None:
    assert degenerate_reason("no no no, that's not what I meant!") is None


def test_gateway_rejects_doom_loop(client: TestClient) -> None:
    contact = client.post(
        "/v1/contacts",
        json={"identities": [{"channel": "sms", "address": "+15556660001"}]},
    ).json()
    conversation = client.post("/v1/conversations", json={"contact_id": contact["id"]}).json()

    rejected = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"body": "I will follow up on that. " * 40},
    )
    assert rejected.status_code == 422
    assert "repetition guard" in rejected.json()["detail"]

    accepted = client.post(
        f"/v1/conversations/{conversation['id']}/messages", json={"body": VARIED_LONG}
    )
    assert accepted.status_code == 201, accepted.text


def test_guard_can_be_disabled(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'noguard.db'}",
        repetition_guard=False,
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        contact = client.post(
            "/v1/contacts",
            json={"identities": [{"channel": "sms", "address": "+15556660002"}]},
        ).json()
        conversation = client.post(
            "/v1/conversations", json={"contact_id": contact["id"]}
        ).json()
        resp = client.post(
            f"/v1/conversations/{conversation['id']}/messages",
            json={"body": "I will follow up on that. " * 40},
        )
        assert resp.status_code == 201
