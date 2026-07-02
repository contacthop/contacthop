"""In-process, DB-backed follow-up scheduler.

Follow-ups are persisted rows, so they survive restarts; a poll loop fires the
due ones. This keeps a single-process deployment dependency-free — swap in a
Redis/arq worker for horizontal scale without changing the data model.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy import select

from contacthop.config import Settings
from contacthop.db.session import Database
from contacthop.domain.enums import ChannelType, EventType, FollowUpStatus
from contacthop.domain.models import Conversation, ConversationEvent, FollowUp, utcnow
from contacthop.domain.schemas import AgentNotification
from contacthop.orchestrator.conversation import notify_agent
from contacthop.orchestrator.escalation import next_channel
from contacthop.orchestrator.windows import open_channels

logger = logging.getLogger("contacthop.scheduler")


class FollowUpScheduler:
    def __init__(
        self, db: Database, settings: Settings, configured_channels: set[ChannelType]
    ) -> None:
        self.db = db
        self.settings = settings
        self.configured_channels = configured_channels
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.settings.follow_up_poll_interval)
            try:
                await self.fire_due()
            except Exception:
                logger.exception("follow-up sweep failed")

    async def fire_due(self) -> int:
        """Fire every pending follow-up whose deadline has passed. Returns count fired."""
        notifications: list[AgentNotification] = []
        async with self.db.session() as session:
            result = await session.execute(
                select(FollowUp).where(
                    FollowUp.status == FollowUpStatus.PENDING,
                    FollowUp.due_at <= utcnow(),
                )
            )
            for follow_up in result.scalars():
                conversation = await session.get(Conversation, follow_up.conversation_id)
                if conversation is None:
                    follow_up.status = FollowUpStatus.CANCELLED
                    continue
                available = {i.channel for i in conversation.contact.identities}
                # Suggest only channels currently inside their send window.
                open_now = open_channels(
                    self.settings, conversation.contact, self.configured_channels
                )
                suggested = next_channel(
                    conversation.current_channel, available, open_now
                )
                follow_up.status = FollowUpStatus.FIRED
                payload = {
                    "attempt": follow_up.attempt,
                    "no_reply_on": conversation.current_channel,
                    "suggested_channel": suggested,
                }
                session.add(
                    ConversationEvent(
                        conversation_id=conversation.id,
                        type=EventType.ESCALATION,
                        payload=payload,
                    )
                )
                notifications.append(
                    AgentNotification(
                        event="conversation.follow_up.due",
                        conversation_id=conversation.id,
                        contact_id=conversation.contact_id,
                        payload=payload,
                    )
                )
            await session.commit()

        for notification in notifications:
            await notify_agent(self.settings, notification)
        if notifications:
            logger.info("fired %d follow-up(s)", len(notifications))
        return len(notifications)
