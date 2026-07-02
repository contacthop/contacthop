"""Minimal ContactHop agent: echoes every inbound message, escalates on silence.

Run the harness:   contacthop serve
Run this agent:    CONTACTHOP_AGENT_WEBHOOK_URL=http://127.0.0.1:9000/ \
                   uvicorn examples.echo_agent:app --port 9000
"""

from contacthop.sdk import Agent

hop = Agent(base_url="http://127.0.0.1:8000")


@hop.on_message
async def echo(ctx, message):
    await ctx.send(f"You said: {message.body}", follow_up_after=3600)


@hop.on_follow_up
async def nudge(ctx, payload):
    await ctx.send(
        "Just checking in — still there?",
        channel=payload["suggested_channel"],
    )


app = hop.app
