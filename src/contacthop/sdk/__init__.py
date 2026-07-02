"""ContactHop SDK: a typed async client and an agent webhook app.

Client (call the harness):

    from contacthop.sdk import ContactHopClient

    async with ContactHopClient("http://localhost:8000") as hop:
        contact = await hop.create_contact(identities=[("sms", "+15551234567")])
        conversation = await hop.create_conversation(contact.id, goal="schedule a demo")
        await hop.send(conversation.id, "Hi! Does Tuesday work?", follow_up_after=3600)

Agent (react to conversation events):

    from contacthop.sdk import Agent

    hop = Agent(base_url="http://localhost:8000")

    @hop.on_message
    async def handle(ctx, message):
        await ctx.send(f"You said: {message.body}")

    # run with: uvicorn my_agent:app  (app = hop.app)
    # and point CONTACTHOP_AGENT_WEBHOOK_URL at it.
"""

from contacthop.sdk.agent import Agent, ConversationContext
from contacthop.sdk.client import ContactHopClient, ContactHopError

__all__ = ["Agent", "ContactHopClient", "ContactHopError", "ConversationContext"]
