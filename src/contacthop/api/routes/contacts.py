from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from contacthop.api.deps import SessionDep
from contacthop.domain.models import ChannelIdentity, Contact
from contacthop.domain.schemas import ChannelIdentityCreate, ContactCreate, ContactRead

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


@router.get("/{contact_id}", response_model=ContactRead)
async def get_contact(contact_id: uuid.UUID, session: SessionDep) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    return contact


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
