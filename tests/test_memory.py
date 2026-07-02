"""Memory layer: transcript digest, context endpoint, responsiveness stats."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from contacthop.domain.enums import ChannelType, Direction, Urgency
from contacthop.memory.stats import reply_latencies
from contacthop.orchestrator.policy import PolicyContext, decide

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_reply_latencies_median_per_channel() -> None:
    stats = reply_latencies(
        [
            (ChannelType.SMS, Direction.OUTBOUND, at(0)),
            (ChannelType.SMS, Direction.INBOUND, at(2)),  # 120s
            (ChannelType.SMS, Direction.OUTBOUND, at(10)),
            (ChannelType.SMS, Direction.INBOUND, at(14)),  # 240s
            (ChannelType.EMAIL, Direction.OUTBOUND, at(20)),
            (ChannelType.EMAIL, Direction.INBOUND, at(80)),  # 3600s
        ]
    )
    assert stats[ChannelType.SMS] == 180.0  # median of 120, 240
    assert stats[ChannelType.EMAIL] == 3600.0


def test_reply_latencies_measures_from_first_unanswered_outbound() -> None:
    stats = reply_latencies(
        [
            (ChannelType.SMS, Direction.OUTBOUND, at(0)),
            (ChannelType.SMS, Direction.OUTBOUND, at(5)),  # nudge doesn't reset the clock
            (ChannelType.SMS, Direction.INBOUND, at(10)),
        ]
    )
    assert stats[ChannelType.SMS] == 600.0


def test_reply_latencies_ignores_unanswered_and_unprompted() -> None:
    stats = reply_latencies(
        [
            (ChannelType.SMS, Direction.INBOUND, at(0)),  # unprompted
            (ChannelType.EMAIL, Direction.OUTBOUND, at(1)),  # never answered
        ]
    )
    assert stats == {}


def test_high_urgency_prefers_measured_fastest_channel() -> None:
    decision = decide(
        PolicyContext(
            current_channel=ChannelType.SMS,
            available_channels={ChannelType.SMS, ChannelType.EMAIL},
            configured_channels={ChannelType.SMS, ChannelType.EMAIL},
            urgency=Urgency.HIGH,
            responsiveness={ChannelType.SMS: 900.0, ChannelType.EMAIL: 60.0},
        )
    )
    assert decision.channel is ChannelType.EMAIL
    assert "fastest" in decision.reason


def test_context_endpoint_digests_old_and_keeps_recent_verbatim(client: TestClient) -> None:
    contact = client.post(
        "/v1/contacts",
        json={"identities": [{"channel": "sms", "address": "+15553334444"}]},
    ).json()
    conversation = client.post(
        "/v1/conversations",
        json={"contact_id": contact["id"], "goal": "long chat"},
    ).json()

    for i in range(8):
        client.post(
            f"/v1/conversations/{conversation['id']}/messages", json={"body": f"message {i}"}
        )

    ctx = client.get(f"/v1/conversations/{conversation['id']}/context?recent=3").json()
    assert ctx["goal"] == "long chat"
    assert [m["body"] for m in ctx["recent_messages"]] == ["message 5", "message 6", "message 7"]
    # older 5 messages are digested, one line each
    assert ctx["summary"].count("[sms] agent:") == 5
    assert "message 0" in ctx["summary"]


def test_contact_stats_endpoint(client: TestClient) -> None:
    contact = client.post(
        "/v1/contacts",
        json={"identities": [{"channel": "sms", "address": "+15556667777"}]},
    ).json()
    conversation = client.post(
        "/v1/conversations", json={"contact_id": contact["id"]}
    ).json()

    client.post(f"/v1/conversations/{conversation['id']}/messages", json={"body": "ping"})
    client.post(
        "/webhooks/twilio/sms",
        data={"From": "+15556667777", "To": "+15550000000", "Body": "pong"},
    )

    stats = client.get(f"/v1/contacts/{contact['id']}/stats").json()
    assert "sms" in stats["median_reply_seconds"]
    assert stats["median_reply_seconds"]["sms"] >= 0.0
