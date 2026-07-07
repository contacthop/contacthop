"""Agent-side webhook app: decorate handlers, mount the ASGI app, done.

ContactHop pushes ``AgentNotification`` events to the agent's webhook URL;
``Agent`` turns those into typed handler calls with a bound conversation
context, so agent code never touches HTTP plumbing.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI

from contacthop.domain.enums import ChannelType, Urgency
from contacthop.domain.schemas import (
    AgentNotification,
    ChannelSessionRead,
    ConversationContextRead,
    ConversationRead,
    MemoryFact,
    MessageRead,
)
from contacthop.sdk.client import ContactHopClient

logger = logging.getLogger("contacthop.sdk")


class ConversationContext:
    """The conversation a notification belongs to, with reply methods bound."""

    def __init__(
        self, client: ContactHopClient, conversation_id: uuid.UUID, contact_id: uuid.UUID
    ) -> None:
        self.client = client
        self.conversation_id = conversation_id
        self.contact_id = contact_id

    async def send(
        self,
        body: str,
        *,
        channel: ChannelType | str | None = None,
        urgency: Urgency | str = Urgency.NORMAL,
        follow_up_after: float | None = None,
    ) -> MessageRead:
        return await self.client.send(
            self.conversation_id,
            body,
            channel=channel,
            urgency=urgency,
            follow_up_after=follow_up_after,
        )

    async def call(self, body: str | None = None) -> ChannelSessionRead:
        return await self.client.call(self.conversation_id, body)

    async def switch(
        self, channel: ChannelType | str, reason: str = "agent requested"
    ) -> ConversationRead:
        return await self.client.switch(self.conversation_id, channel, reason)

    async def transcript(self) -> list[MessageRead]:
        return await self.client.transcript(self.conversation_id)

    async def context(self, recent: int = 20) -> ConversationContextRead:
        return await self.client.context(self.conversation_id, recent)

    async def remember(self, text: str, topic: str | None = None) -> MemoryFact:
        """Store a durable fact about this conversation's contact."""
        return await self.client.remember(
            self.contact_id, text, topic=topic, conversation_id=self.conversation_id
        )

    async def recall(self, topic: str | None = None) -> list[MemoryFact]:
        return await self.client.recall(self.contact_id, topic=topic)


MessageHandler = Callable[[ConversationContext, MessageRead], Awaitable[None]]
FollowUpHandler = Callable[[ConversationContext, dict[str, Any]], Awaitable[None]]
EventHandler = Callable[[ConversationContext, AgentNotification], Awaitable[None]]


class Agent:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_key: str | None = None,
        client: ContactHopClient | None = None,
    ) -> None:
        self.client = client or ContactHopClient(base_url, api_key=api_key)
        self._message_handlers: list[MessageHandler] = []
        self._follow_up_handlers: list[FollowUpHandler] = []
        self._event_handlers: list[EventHandler] = []
        self._app: FastAPI | None = None

    # -- decorators ----------------------------------------------------------

    def on_message(self, fn: MessageHandler) -> MessageHandler:
        """Called with (ctx, message) for every inbound human message, any channel."""
        self._message_handlers.append(fn)
        return fn

    def on_follow_up(self, fn: FollowUpHandler) -> FollowUpHandler:
        """Called with (ctx, payload) when a no-reply follow-up fires.

        ``payload`` carries ``attempt``, ``no_reply_on``, and ``suggested_channel``.
        """
        self._follow_up_handlers.append(fn)
        return fn

    def on_event(self, fn: EventHandler) -> EventHandler:
        """Catch-all: called with (ctx, notification) for every event."""
        self._event_handlers.append(fn)
        return fn

    # -- dispatch -------------------------------------------------------------

    async def dispatch(self, notification: AgentNotification) -> None:
        ctx = ConversationContext(
            self.client, notification.conversation_id, notification.contact_id
        )
        if notification.event == "conversation.message.received" and notification.message:
            for message_handler in self._message_handlers:
                await message_handler(ctx, notification.message)
        elif notification.event == "conversation.follow_up.due":
            for follow_up_handler in self._follow_up_handlers:
                await follow_up_handler(ctx, notification.payload)
        for event_handler in self._event_handlers:
            await event_handler(ctx, notification)

    @property
    def app(self) -> FastAPI:
        """The ASGI app to run (``uvicorn my_agent:app``) and point
        ``CONTACTHOP_AGENT_WEBHOOK_URL`` at."""
        if self._app is None:
            self._app = self._build_app()
        return self._app

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="ContactHop Agent", docs_url=None, redoc_url=None)

        @app.post("/")
        async def receive(notification: AgentNotification) -> dict[str, str]:
            try:
                await self.dispatch(notification)
            except Exception:
                # Never bounce the harness's webhook delivery on handler bugs.
                logger.exception("agent handler failed for event %s", notification.event)
            return {"status": "ok"}

        return app
