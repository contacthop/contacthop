"""ContactHop as an MCP server: the harness's capabilities as standard tools.

Any MCP-capable agent runtime — Qwen-Agent, Claude, OpenAI Agents SDK, custom
loops — can drive cross-channel conversations without ContactHop knowing or
caring which model is behind it.

The server is a thin client of the REST API (via the SDK), never the database:
every send still passes through the gateway backstops — quiet hours, rate
limits, consent, auth — no matter what model is calling the tools. Backstop
rejections surface as tool errors carrying the reason, so the model can adapt
(e.g. "sms is outside its send window" → try email or wait).

Run it:  contacthop mcp --base-url https://hop.example.com --api-key $KEY
"""

from __future__ import annotations

from typing import Any

from contacthop.sdk import ContactHopClient

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise RuntimeError(
        "the MCP server requires the mcp package: pip install 'contacthop[mcp]'"
    ) from exc

INSTRUCTIONS = """\
ContactHop holds long-running conversations with humans across SMS, email, and
voice, hopping channels while keeping one transcript. Typical flow: find or
create a contact, start a conversation with a goal, then send messages —
usually WITHOUT specifying a channel so the policy engine picks the best one.
Before composing a reply, call get_context for the conversation summary, recent
messages, and remembered facts about the contact. Use remember to store durable
facts worth knowing next time. Sends can be rejected by safety backstops
(quiet hours, rate limits, opt-outs); the error says why — adapt rather than
retry blindly."""


def build_server(
    base_url: str = "http://127.0.0.1:8000",
    api_key: str | None = None,
    client: ContactHopClient | None = None,
) -> FastMCP:
    hop = client or ContactHopClient(base_url, api_key=api_key)
    server = FastMCP("contacthop", instructions=INSTRUCTIONS)

    def one(model: Any) -> dict[str, Any]:
        result: dict[str, Any] = model.model_dump(mode="json")
        return result

    def many(models: list[Any]) -> list[dict[str, Any]]:
        return [m.model_dump(mode="json") for m in models]

    # ── contacts ──────────────────────────────────────────────────────────

    @server.tool()
    async def list_contacts() -> list[dict[str, Any]]:
        """List known contacts with their channel identities (phone numbers /
        email addresses) and consent state (opted_out means unreachable there)."""
        data: list[dict[str, Any]] = await hop._request("GET", "/v1/contacts?limit=200")
        return data

    @server.tool()
    async def create_contact(
        display_name: str | None = None,
        sms_number: str | None = None,
        email_address: str | None = None,
    ) -> dict[str, Any]:
        """Create a contact. Provide at least one address: an E.164 phone number
        (used for SMS and voice calls) and/or an email address. Fails with 409
        if an address already belongs to another contact — reuse that contact."""
        identities: list[tuple[str, str] | dict[str, str]] = []
        if sms_number:
            identities.append(("sms", sms_number))
        if email_address:
            identities.append(("email", email_address))
        return one(await hop.create_contact(display_name, identities=identities))

    @server.tool()
    async def contact_stats(contact_id: str) -> dict[str, Any]:
        """How quickly this contact replies on each channel (median seconds).
        Useful for choosing a channel or judging whether to follow up."""
        return one(await hop.stats(contact_id))

    # ── conversations ─────────────────────────────────────────────────────

    @server.tool()
    async def list_conversations(status: str | None = None) -> list[dict[str, Any]]:
        """List conversations, newest first. Optional status filter:
        'active', 'paused', or 'closed'."""
        path = "/v1/conversations?limit=200" + (f"&status={status}" if status else "")
        data: list[dict[str, Any]] = await hop._request("GET", path)
        return data

    @server.tool()
    async def start_conversation(
        contact_id: str, goal: str, channel: str = "sms"
    ) -> dict[str, Any]:
        """Start a new conversation with a contact. The goal describes what the
        conversation is trying to accomplish (it also seeds email subjects).
        channel is where it begins: 'sms', 'email', or 'voice'."""
        return one(await hop.create_conversation(contact_id, goal=goal, channel=channel))

    @server.tool()
    async def get_context(conversation_id: str) -> dict[str, Any]:
        """Everything needed to compose the next turn: the conversation goal,
        current channel, a digest of older history, the recent messages
        verbatim, and remembered facts about the contact. Call this before
        replying."""
        return one(await hop.context(conversation_id))

    @server.tool()
    async def send_message(
        conversation_id: str,
        body: str,
        channel: str | None = None,
        urgency: str = "normal",
        follow_up_after_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Send a message to the conversation's human. Omit channel (recommended)
        and the policy engine picks the best one — long-form content goes to
        email, high urgency ('high') prefers the contact's fastest channel.
        Set follow_up_after_seconds to be notified if the human doesn't reply
        in time. Rejections (quiet hours, rate limit, opt-out) explain why."""
        return one(
            await hop.send(
                conversation_id,
                body,
                channel=channel,
                urgency=urgency,
                follow_up_after=follow_up_after_seconds,
            )
        )

    @server.tool()
    async def switch_channel(
        conversation_id: str, channel: str, reason: str = "agent requested"
    ) -> dict[str, Any]:
        """Move the conversation to another channel ('sms', 'email', 'voice');
        subsequent sends default there. The switch is logged with the reason."""
        return one(await hop.switch(conversation_id, channel, reason))

    @server.tool()
    async def place_call(conversation_id: str, opening_line: str | None = None) -> dict[str, Any]:
        """Dial the contact now (voice). The opening line is spoken when they
        answer; their speech comes back as inbound messages. While the call is
        open, send_message speaks into it. One live call per conversation."""
        return one(await hop.call(conversation_id, opening_line))

    # ── memory ────────────────────────────────────────────────────────────

    @server.tool()
    async def remember(
        contact_id: str,
        fact: str,
        topic: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Store a durable fact about a contact (e.g. 'prefers 4pm meetings').
        Facts come back automatically in get_context. Add a short topic to
        make it findable across contacts; pass the conversation_id it came
        from for provenance."""
        return one(
            await hop.remember(contact_id, fact, topic=topic, conversation_id=conversation_id)
        )

    @server.tool()
    async def recall(contact_id: str, topic: str | None = None) -> list[dict[str, Any]]:
        """Recall remembered facts about a contact, optionally filtered by topic."""
        return many(await hop.recall(contact_id, topic=topic))

    @server.tool()
    async def recall_topic(topic: str) -> list[dict[str, Any]]:
        """Recall everything remembered about a topic across ALL contacts —
        e.g. who has discussed 'pricing' and what was said."""
        return many(await hop.recall_topic(topic))

    return server
