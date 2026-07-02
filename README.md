# ContactHop

ContactHop is an open-source agentic communication harness that enables personal AI agents to seamlessly communicate and switch modalities during long-running communications across SMS, email, and voice channels.

Conversations can naturally begin on a phone call, continue through SMS throughout the day, escalate to email when needed, and transition back into voice — all while maintaining shared context, memory, and conversational continuity.

ContactHop provides a unified orchestration layer for persistent AI-to-human communication workflows, allowing autonomous agents to dynamically choose the best communication medium based on context, urgency, responsiveness, and user preference.

Designed for the next generation of personal AI systems, autonomous operators, executive assistants, and agentic infrastructure.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design.

## Installation

ContactHop is a standard Python package (Python 3.11+):

```bash
uv add contacthop          # with uv
pip install contacthop     # with pip
```

From source:

```bash
git clone https://github.com/contacthop/contacthop
cd contacthop
uv sync                    # or: pip install -e .
```

Optional extras: `contacthop[postgres]` for asyncpg support.

## Quickstart

Run the API server (defaults to SQLite and the zero-credential `console` SMS adapter, which logs outbound messages instead of sending them):

```bash
contacthop serve
# interactive docs at http://127.0.0.1:8000/docs
```

Create a contact and start a conversation:

```bash
curl -s localhost:8000/v1/contacts -H 'content-type: application/json' -d '{
  "display_name": "Ada Lovelace",
  "identities": [{"channel": "sms", "address": "+15551234567"}]
}'

curl -s localhost:8000/v1/conversations -H 'content-type: application/json' -d '{
  "contact_id": "<contact-id>", "goal": "schedule a demo"
}'
```

Send a message as the agent — omit `channel` and the policy engine picks one (long-form content hops to email, high urgency prefers SMS, otherwise it stays on the current channel). Add `follow_up_after` (seconds) to schedule a no-reply escalation:

```bash
curl -s localhost:8000/v1/conversations/<id>/messages \
  -H 'content-type: application/json' \
  -d '{"body": "Hi Ada — following up about the demo.", "follow_up_after": 7200}'
```

If the human doesn't reply in time, ContactHop logs an `escalation` event with a suggested next channel (SMS → email → voice ladder) and notifies your agent, which decides what to send next. Replies cancel pending follow-ups automatically.

Inbound messages arrive via provider webhooks — `POST /webhooks/twilio/sms` for SMS, and the provider-agnostic `POST /webhooks/email/inbound` (normalized JSON) for email — and are pushed to your agent runtime's webhook (`CONTACTHOP_AGENT_WEBHOOK_URL`). Outbound email carries `Message-ID`/`In-Reply-To`/`References` headers so the human's mail client keeps one thread.

Other useful endpoints: `POST /v1/conversations/<id>/switch` to move a conversation to another channel explicitly, and `POST /v1/contacts/<id>/identities` to attach additional addresses to a contact (cross-channel identity resolution).

### Voice calls

Originate a call with an opening line; the call becomes an open channel session:

```bash
curl -s localhost:8000/v1/conversations/<id>/call \
  -H 'content-type: application/json' -d '{"body": "Hi Ada, quick question about the demo."}'
```

While the call is open, agent messages to the conversation are queued and spoken into the call; the human's speech comes back as inbound voice messages in the same transcript. v1 voice uses Twilio's built-in speech recognition (`<Gather input="speech">`) and TTS (`<Say>`) — no separate speech providers needed. The Twilio voice adapter requires `CONTACTHOP_PUBLIC_BASE_URL` so Twilio can reach the call webhooks; a streaming Media Streams pipeline (lower latency, barge-in) is the planned upgrade path.

## Python SDK

The SDK ships inside the same package: a typed async client plus a decorator-based agent app.

```python
from contacthop.sdk import Agent

hop = Agent(base_url="http://127.0.0.1:8000")

@hop.on_message
async def handle(ctx, message):
    context = await ctx.context()          # digest + recent messages, prompt-ready
    reply = await my_llm(context, message)  # your model, your loop
    await ctx.send(reply, follow_up_after=3600)

@hop.on_follow_up
async def nudge(ctx, payload):
    await ctx.send("Still there?", channel=payload["suggested_channel"])

app = hop.app  # run: uvicorn my_agent:app --port 9000
```

Point `CONTACTHOP_AGENT_WEBHOOK_URL=http://127.0.0.1:9000/` at it and every inbound message — SMS, email, or spoken voice turn — lands in `handle`. The client is also usable standalone (`ContactHopClient`) for scripting: `create_contact`, `create_conversation`, `send`, `call`, `switch`, `transcript`, `context`, `stats`. See `examples/echo_agent.py`.

The policy engine also learns per-contact responsiveness: `GET /v1/contacts/<id>/stats` exposes median reply latency per channel, and high-urgency messages automatically use the contact's fastest-responding channel once data exists.

## Configuration

Settings come from environment variables (or a `.env` file), prefixed with `CONTACTHOP_`. Start from the annotated template:

```bash
cp demo.env .env
```

| Variable | Default | Purpose |
|---|---|---|
| `CONTACTHOP_DATABASE_URL` | `sqlite+aiosqlite:///contacthop.db` | Any async SQLAlchemy URL (use `postgresql+asyncpg://…` in production) |
| `CONTACTHOP_AUTO_CREATE_TABLES` | `true` | Create missing tables at startup (dev); set `false` and use `contacthop migrate` in production |
| `CONTACTHOP_SMS_ADAPTER` | `console` | `console` or `twilio` |
| `CONTACTHOP_EMAIL_ADAPTER` | `console` | `none`, `console`, or `smtp` |
| `CONTACTHOP_VOICE_ADAPTER` | `console` | `none`, `console`, or `twilio` (twilio also needs `PUBLIC_BASE_URL`) |
| `CONTACTHOP_TWILIO_ACCOUNT_SID` / `_AUTH_TOKEN` / `_FROM_NUMBER` | — | Required for the Twilio adapter |
| `CONTACTHOP_SMTP_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` / `_FROM_ADDRESS` / `_STARTTLS` | — | Required for the SMTP adapter (host + from address at minimum) |
| `CONTACTHOP_EMAIL_INBOUND_TOKEN` | — | Shared secret (`X-ContactHop-Token`) guarding the inbound email webhook |
| `CONTACTHOP_PUBLIC_BASE_URL` | — | Public URL of this deployment, used for webhook signature validation |
| `CONTACTHOP_AGENT_WEBHOOK_URL` | — | Where conversation events are pushed for your agent |
| `CONTACTHOP_FOLLOW_UP_POLL_INTERVAL` | `5.0` | Seconds between scheduler sweeps for due follow-ups |
| `CONTACTHOP_SEND_WINDOW_SMS` / `_EMAIL` / `_VOICE` | — (always) | Per-channel send window (quiet hours), `HH:MM-HH:MM`, may wrap midnight |
| `CONTACTHOP_DEFAULT_TIMEZONE` | `UTC` | IANA timezone the windows are evaluated in when a contact has none |
| `CONTACTHOP_API_KEYS` | — (open) | Comma-separated Bearer tokens required on the `/v1` management API |
| `CONTACTHOP_MAX_MESSAGES_PER_HOUR` | `30` | Per-contact outbound cap, rolling hour, all channels; `0` = unlimited |

### Send windows (quiet hours)

Windows like `CONTACTHOP_SEND_WINDOW_SMS=08:00-17:00` restrict when each channel may be used, evaluated in the contact's timezone (`preferences["timezone"]`, falling back to `DEFAULT_TIMEZONE`). Contacts can carry per-channel nuance that overrides the deployment defaults:

```json
{"timezone": "America/Chicago", "send_windows": {"sms": "09:00-20:00", "voice": "12:00-13:00"}}
```

Enforcement is a hard backstop in the outbound gateway — below the policy engine, so even a buggy agent can't text someone at 3am:

- explicit sends on a closed channel are rejected (422 with the window in the detail),
- policy-routed sends automatically hop to a channel that's open (SMS closed at 6pm, email open → the reply goes out as email),
- placing calls outside the voice window is rejected, and escalation suggestions skip closed channels,
- the one exemption: speaking into an **already-live** voice call is always allowed — the human is on the line.

### Auth, rate limits & delivery receipts

Set `CONTACTHOP_API_KEYS` and every `/v1` endpoint requires `Authorization: Bearer <key>` (the SDK's `api_key` parameter sends it). Webhooks and `/health` are unaffected — webhooks authenticate with provider signatures and shared secrets instead.

`CONTACTHOP_MAX_MESSAGES_PER_HOUR` caps outbound messages (and call originations) per contact across all channels in a rolling hour — the anti-spam half of the gateway backstop. Inbound messages never count against it, and contacts can carry their own limit via `preferences: {"max_messages_per_hour": 10}`. Exceeding it returns 429.

With `PUBLIC_BASE_URL` set, Twilio delivery receipts flow into `POST /webhooks/twilio/sms/status` and keep `Message.delivery_status` honest (`sent` → `delivered`/`failed`). Failed deliveries are logged as conversation events and pushed to the agent as `conversation.message.failed`, so it can retry on another channel.

### Database migrations

Schema changes are managed with Alembic, and the migration scripts ship inside the package:

```bash
contacthop migrate            # upgrade to head (uses CONTACTHOP_DATABASE_URL)
contacthop migrate <revision> # upgrade/downgrade to a specific revision
```

For production, set `CONTACTHOP_AUTO_CREATE_TABLES=false` and run `contacthop migrate` as a deploy step. If you started on a dev database created by `auto_create_tables`, adopt migrations with `uv run alembic stamp head` once. New revisions are autogenerated from the repo: `uv run alembic revision --autogenerate -m "describe change"` — CI includes a drift guard that fails if models change without a matching migration.

## Development

```bash
uv sync            # installs the package + dev group (pytest, ruff, mypy)
uv run pytest
uv run ruff check .
```

CI (GitHub Actions) runs ruff, the test suite on Python 3.11–3.13, and a package build on every push and pull request.

