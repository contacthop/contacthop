from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from contacthop.api.deps import PrincipalDep, SessionDep, ensure_visible
from contacthop.domain.models import ChannelIdentity, Contact
from contacthop.domain.schemas import (
    ChannelIdentityCreate,
    ContactCreate,
    ContactRead,
    ContactStatsRead,
)
from contacthop.memory.stats import channel_responsiveness

router = APIRouter(prefix="/v1/contacts", tags=["contacts"])


async def _reject_taken_addresses(
    session: SessionDep, identities: list[ChannelIdentityCreate]
) -> None:
    """An address belongs to exactly one contact — inbound identity resolution
    depends on it (DB-enforced too; this yields a friendly 409 instead)."""
    for identity in identities:
        existing = await session.execute(
            select(ChannelIdentity).where(
                ChannelIdentity.channel == identity.channel,
                ChannelIdentity.address == identity.address,
            )
        )
        if existing.scalars().first() is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{identity.channel} address {identity.address!r} is already "
                "registered to a contact",
            )


@router.post("", response_model=ContactRead, status_code=201)
async def create_contact(
    payload: ContactCreate, session: SessionDep, principal: PrincipalDep
) -> Contact:
    unique_in_payload = {(i.channel, i.address) for i in payload.identities}
    if len(unique_in_payload) != len(payload.identities):
        raise HTTPException(status_code=422, detail="duplicate identities in payload")
    await _reject_taken_addresses(session, payload.identities)

    contact = Contact(
        display_name=payload.display_name,
        preferences=payload.preferences,
        agent_id=principal.agent_id,
    )
    contact.identities = [
        ChannelIdentity(channel=i.channel, address=i.address) for i in payload.identities
    ]
    session.add(contact)
    await session.flush()
    return contact


@router.get("", response_model=list[ContactRead])
async def list_contacts(
    session: SessionDep,
    principal: PrincipalDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Contact]:
    query = select(Contact).order_by(Contact.created_at.desc(), Contact.id)
    if principal.agent is not None:
        query = query.where(Contact.agent_id == principal.agent.id)
    result = await session.execute(query.limit(limit).offset(offset))
    return list(result.scalars())


@router.get("/{contact_id}", response_model=ContactRead)
async def get_contact(
    contact_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    ensure_visible(contact.agent_id, principal)
    return contact


@router.get("/{contact_id}/stats", response_model=ContactStatsRead)
async def get_stats(
    contact_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> ContactStatsRead:
    """How fast this contact replies on each channel (median seconds)."""
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    ensure_visible(contact.agent_id, principal)
    return ContactStatsRead(
        contact_id=contact_id,
        median_reply_seconds=await channel_responsiveness(session, contact_id),
    )


@router.post("/{contact_id}/identities", response_model=ContactRead, status_code=201)
async def add_identity(
    contact_id: uuid.UUID,
    payload: ChannelIdentityCreate,
    session: SessionDep,
    principal: PrincipalDep,
) -> Contact:
    """Attach another channel address to a contact — cross-channel identity resolution."""
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    ensure_visible(contact.agent_id, principal)
    await _reject_taken_addresses(session, [payload])
    contact.identities.append(
        ChannelIdentity(channel=payload.channel, address=payload.address)
    )
    await session.flush()
    return contact
