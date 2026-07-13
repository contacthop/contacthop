"""Durable agent webhook delivery (outbox pattern).

Notifications are persisted before any network attempt, delivered immediately
in the background, and retried by the scheduler sweep with exponential backoff
(30s, 1m, 2m, … capped at 2h) until they succeed or exhaust their attempts —
a briefly-down agent runtime loses nothing. Exhausted deliveries are the dead
letter queue, visible and re-armable via /v1/deliveries.

Agents should treat notifications as at-least-once: a delivery that times out
after the agent processed it will be retried.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contacthop.config import Settings
from contacthop.db.session import Database
from contacthop.domain.enums import WebhookDeliveryStatus
from contacthop.domain.models import AgentDelivery, utcnow
from contacthop.domain.schemas import AgentNotification

logger = logging.getLogger("contacthop.notifier")

BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 7200
SWEEP_BATCH = 20


def backoff_seconds(attempts: int) -> int:
    return min(BACKOFF_BASE_SECONDS * 2 ** max(attempts - 1, 0), BACKOFF_CAP_SECONDS)


async def _post(url: str, payload: dict) -> None:
    """One delivery attempt; raises on network errors and non-2xx responses."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


async def enqueue_notification(
    session: AsyncSession, settings: Settings, notification: AgentNotification
) -> AgentDelivery | None:
    """Persist the notification in the caller's transaction. Returns None when
    no agent webhook is configured (nothing to deliver, nothing to store)."""
    if not settings.agent_webhook_url:
        return None
    delivery = AgentDelivery(
        event=notification.event,
        conversation_id=notification.conversation_id,
        payload=notification.model_dump(mode="json"),
    )
    session.add(delivery)
    await session.flush()
    return delivery


async def attempt_delivery(db: Database, settings: Settings, delivery_id: uuid.UUID) -> bool:
    """Try to deliver one pending notification; on failure schedule the retry.

    Runs outside any request transaction (background task or scheduler sweep).
    """
    if not settings.agent_webhook_url:
        return False
    async with db.session() as session:
        delivery = await session.get(AgentDelivery, delivery_id)
        if delivery is None or delivery.status != WebhookDeliveryStatus.PENDING:
            return False
        delivery.attempts += 1
        try:
            await _post(settings.agent_webhook_url, delivery.payload)
        except Exception as exc:
            delivery.last_error = str(exc)[:500]
            if delivery.attempts >= settings.agent_webhook_max_attempts:
                delivery.status = WebhookDeliveryStatus.EXHAUSTED
                logger.error(
                    "agent webhook delivery exhausted after %d attempts (%s): %s",
                    delivery.attempts,
                    delivery.event,
                    delivery.last_error,
                )
            else:
                delivery.next_attempt_at = utcnow() + timedelta(
                    seconds=backoff_seconds(delivery.attempts)
                )
            await session.commit()
            return False
        delivery.status = WebhookDeliveryStatus.DELIVERED
        delivery.delivered_at = utcnow()
        delivery.last_error = None
        await session.commit()
        return True


async def deliver_due(db: Database, settings: Settings) -> int:
    """Retry every pending delivery whose backoff has elapsed. Returns count delivered."""
    if not settings.agent_webhook_url:
        return 0
    async with db.session() as session:
        result = await session.execute(
            select(AgentDelivery.id)
            .where(
                AgentDelivery.status == WebhookDeliveryStatus.PENDING,
                AgentDelivery.next_attempt_at <= utcnow(),
            )
            .order_by(AgentDelivery.next_attempt_at)
            .limit(SWEEP_BATCH)
        )
        due = [row[0] for row in result.all()]
    delivered = 0
    for delivery_id in due:
        if await attempt_delivery(db, settings, delivery_id):
            delivered += 1
    return delivered
