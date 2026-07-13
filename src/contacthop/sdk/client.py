"""Typed async client for the ContactHop REST API."""

from __future__ import annotations

import uuid
from types import TracebackType
from typing import Any

import httpx

from contacthop.domain.enums import ChannelType, Urgency
from contacthop.domain.schemas import (
    ChannelSessionRead,
    ContactMemoryFact,
    ContactRead,
    ContactStatsRead,
    ConversationContextRead,
    ConversationRead,
    EventRead,
    MemoryFact,
    MessageRead,
)

Identity = tuple[str, str] | dict[str, str]


class ContactHopError(Exception):
    """The harness rejected a request; ``status_code`` and ``detail`` say why."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class ContactHopClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=30
        )

    async def __aenter__(self) -> ContactHopClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, json: Any = None) -> Any:
        resp = await self._client.request(method, path, json=json)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise ContactHopError(resp.status_code, str(detail))
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # -- contacts ----------------------------------------------------------

    async def create_contact(
        self,
        display_name: str | None = None,
        identities: list[Identity] | None = None,
        preferences: dict[str, Any] | None = None,
    ) -> ContactRead:
        payload = {
            "display_name": display_name,
            "preferences": preferences or {},
            "identities": [
                i if isinstance(i, dict) else {"channel": i[0], "address": i[1]}
                for i in (identities or [])
            ],
        }
        return ContactRead.model_validate(await self._request("POST", "/v1/contacts", payload))

    async def get_contact(self, contact_id: uuid.UUID | str) -> ContactRead:
        return ContactRead.model_validate(
            await self._request("GET", f"/v1/contacts/{contact_id}")
        )

    async def add_identity(
        self, contact_id: uuid.UUID | str, channel: ChannelType | str, address: str
    ) -> ContactRead:
        return ContactRead.model_validate(
            await self._request(
                "POST",
                f"/v1/contacts/{contact_id}/identities",
                {"channel": str(channel), "address": address},
            )
        )

    # -- conversations ------------------------------------------------------

    async def create_conversation(
        self,
        contact_id: uuid.UUID | str,
        goal: str | None = None,
        channel: ChannelType | str = ChannelType.SMS,
    ) -> ConversationRead:
        return ConversationRead.model_validate(
            await self._request(
                "POST",
                "/v1/conversations",
                {"contact_id": str(contact_id), "goal": goal, "channel": str(channel)},
            )
        )

    async def get_conversation(self, conversation_id: uuid.UUID | str) -> ConversationRead:
        return ConversationRead.model_validate(
            await self._request("GET", f"/v1/conversations/{conversation_id}")
        )

    async def transcript(self, conversation_id: uuid.UUID | str) -> list[MessageRead]:
        data = await self._request("GET", f"/v1/conversations/{conversation_id}/transcript")
        return [MessageRead.model_validate(m) for m in data]

    async def events(self, conversation_id: uuid.UUID | str) -> list[EventRead]:
        data = await self._request("GET", f"/v1/conversations/{conversation_id}/events")
        return [EventRead.model_validate(e) for e in data]

    async def sessions(self, conversation_id: uuid.UUID | str) -> list[ChannelSessionRead]:
        data = await self._request("GET", f"/v1/conversations/{conversation_id}/sessions")
        return [ChannelSessionRead.model_validate(s) for s in data]

    async def context(
        self, conversation_id: uuid.UUID | str, recent: int = 20
    ) -> ConversationContextRead:
        """Prompt-ready context: digest of older history + recent messages verbatim."""
        return ConversationContextRead.model_validate(
            await self._request(
                "GET", f"/v1/conversations/{conversation_id}/context?recent={recent}"
            )
        )

    async def stats(self, contact_id: uuid.UUID | str) -> ContactStatsRead:
        return ContactStatsRead.model_validate(
            await self._request("GET", f"/v1/contacts/{contact_id}/stats")
        )

    # -- memory ---------------------------------------------------------------

    async def remember(
        self,
        contact_id: uuid.UUID | str,
        text: str,
        topic: str | None = None,
        conversation_id: uuid.UUID | str | None = None,
    ) -> MemoryFact:
        return MemoryFact.model_validate(
            await self._request(
                "POST",
                f"/v1/contacts/{contact_id}/memory",
                {
                    "text": text,
                    "topic": topic,
                    "conversation_id": str(conversation_id) if conversation_id else None,
                },
            )
        )

    async def recall(
        self, contact_id: uuid.UUID | str, topic: str | None = None, limit: int = 50
    ) -> list[MemoryFact]:
        path = f"/v1/contacts/{contact_id}/memory?limit={limit}"
        if topic:
            path += f"&topic={topic}"
        return [MemoryFact.model_validate(f) for f in await self._request("GET", path)]

    async def forget(self, contact_id: uuid.UUID | str, fact_id: uuid.UUID | str) -> None:
        await self._request("DELETE", f"/v1/contacts/{contact_id}/memory/{fact_id}")

    async def recall_topic(self, topic: str, limit: int = 100) -> list[ContactMemoryFact]:
        data = await self._request("GET", f"/v1/memory/topics/{topic}?limit={limit}")
        return [ContactMemoryFact.model_validate(f) for f in data]

    # -- messaging ----------------------------------------------------------

    async def send(
        self,
        conversation_id: uuid.UUID | str,
        body: str,
        *,
        channel: ChannelType | str | None = None,
        urgency: Urgency | str = Urgency.NORMAL,
        follow_up_after: float | None = None,
    ) -> MessageRead:
        """Send an agent reply. Omit ``channel`` and the policy engine decides."""
        return MessageRead.model_validate(
            await self._request(
                "POST",
                f"/v1/conversations/{conversation_id}/messages",
                {
                    "body": body,
                    "channel": str(channel) if channel else None,
                    "urgency": str(urgency),
                    "follow_up_after": follow_up_after,
                },
            )
        )

    async def switch(
        self,
        conversation_id: uuid.UUID | str,
        channel: ChannelType | str,
        reason: str = "agent requested",
    ) -> ConversationRead:
        return ConversationRead.model_validate(
            await self._request(
                "POST",
                f"/v1/conversations/{conversation_id}/switch",
                {"channel": str(channel), "reason": reason},
            )
        )

    async def call(
        self, conversation_id: uuid.UUID | str, body: str | None = None
    ) -> ChannelSessionRead:
        """Originate a voice call; ``body`` is spoken as the opening line."""
        return ChannelSessionRead.model_validate(
            await self._request(
                "POST", f"/v1/conversations/{conversation_id}/call", {"body": body}
            )
        )

    async def health(self) -> dict[str, str]:
        result: dict[str, str] = await self._request("GET", "/health")
        return result

    # -- agent (tenant) management — admin keys only --------------------------

    async def create_agent(self, name: str, webhook_url: str | None = None) -> dict[str, Any]:
        """Create a tenant. The response's 'api_key' is shown once — store it."""
        result: dict[str, Any] = await self._request(
            "POST", "/v1/agents", {"name": name, "webhook_url": webhook_url}
        )
        return result

    async def list_agents(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self._request("GET", "/v1/agents")
        return result

    async def rotate_agent_key(self, agent_id: uuid.UUID | str) -> dict[str, Any]:
        result: dict[str, Any] = await self._request(
            "POST", f"/v1/agents/{agent_id}/rotate-key"
        )
        return result
