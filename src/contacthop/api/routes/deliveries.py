"""Agent webhook delivery outbox: inspect pending/exhausted notifications and
re-arm dead letters after the agent runtime is back up."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from contacthop.api.deps import SessionDep, SettingsDep
from contacthop.domain.enums import WebhookDeliveryStatus
from contacthop.domain.models import AgentDelivery, utcnow
from contacthop.domain.schemas import AgentDeliveryRead
from contacthop.orchestrator.notifier import attempt_delivery

router = APIRouter(prefix="/v1/deliveries", tags=["deliveries"])


@router.get("", response_model=list[AgentDeliveryRead])
async def list_deliveries(
    session: SessionDep,
    status: WebhookDeliveryStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[AgentDelivery]:
    query = select(AgentDelivery).order_by(AgentDelivery.created_at.desc(), AgentDelivery.id)
    if status is not None:
        query = query.where(AgentDelivery.status == status)
    result = await session.execute(query.limit(limit).offset(offset))
    return list(result.scalars())


@router.post("/{delivery_id}/retry", response_model=AgentDeliveryRead)
async def retry_delivery(
    delivery_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> AgentDelivery:
    """Re-arm a delivery (typically an exhausted dead letter) and attempt it now."""
    delivery = await session.get(AgentDelivery, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    if delivery.status is WebhookDeliveryStatus.DELIVERED:
        raise HTTPException(status_code=409, detail="already delivered")

    delivery.status = WebhookDeliveryStatus.PENDING
    delivery.attempts = 0
    delivery.next_attempt_at = utcnow()
    await session.commit()

    await attempt_delivery(request.app.state.db, settings, delivery.id)
    refreshed = await session.get(AgentDelivery, delivery.id, populate_existing=True)
    assert refreshed is not None
    return refreshed
