from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from contacthop.api.deps import SessionDep
from contacthop.domain.models import ChannelIdentity, Contact
from contacthop.domain.schemas import (
    ChannelIdentityCreate,
    ContactCreate,
    ContactRead,
    ContactStatsRead,
)
from contacthop.memory.stats import channel_responsiveness

router = APIRouter(prefix="/v1/contacts", tags=["contacts"])


@router.post("", response_model=ContactRead, status_code=201)
async def create_contact(payload: ContactCreate, session: SessionDep) -> Contact:
    contact = Contact(display_name=payload.display_name, preferences=payload.preferences)
    contact.identities = [
        ChannelIdentity(channel=i.channel, address=i.address) for i in payload.identities
    ]
    session.add(contact)
    await session.flush()
    return contact


@router.get("", response_model=list[ContactRead])
async def list_contacts(
    session: SessionDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Contact]:
    result = await session.execute(
        select(Contact).order_by(Contact.created_at.desc(), Contact.id).limit(limit).offset(offset)
    )
    return list(result.scalars())


@router.get("/{contact_id}", response_model=ContactRead)
async def get_contact(contact_id: uuid.UUID, session: SessionDep) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    return contact


@router.get("/{contact_id}/stats", response_model=ContactStatsRead)
async def get_stats(contact_id: uuid.UUID, session: SessionDep) -> ContactStatsRead:
    """How fast this contact replies on each channel (median seconds)."""
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    return ContactStatsRead(
        contact_id=contact_id,
        median_reply_seconds=await channel_responsiveness(session, contact_id),
    )


@router.post("/{contact_id}/identities", response_model=ContactRead, status_code=201)
async def add_identity(
    contact_id: uuid.UUID, payload: ChannelIdentityCreate, session: SessionDep
) -> Contact:
    """Attach another channel address to a contact — cross-channel identity resolution."""
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    if any(
        i.channel == payload.channel and i.address == payload.address
        for i in contact.identities
    ):
        raise HTTPException(status_code=409, detail="identity already exists on this contact")
    contact.identities.append(
        ChannelIdentity(channel=payload.channel, address=payload.address)
    )
    await session.flush()
    return contact
