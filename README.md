# ContactHop

[![CI](https://github.com/contacthop/contacthop/actions/workflows/ci.yml/badge.svg)](https://github.com/contacthop/contacthop/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

ContactHop is an open-source agentic communication harness that enables personal AI agents to seamlessly communicate and switch modalities during long-running communications across SMS, email, and voice channels.

Conversations can naturally begin on a phone call, continue through SMS throughout the day, escalate to email when needed, and transition back into voice — all while maintaining shared context, memory, and conversational continuity.

ContactHop provides a unified orchestration layer for persistent AI-to-human communication workflows, allowing autonomous agents to dynamically choose the best communication medium based on context, urgency, responsiveness, and user preference.

Designed for the next generation of personal AI systems, autonomous operators, executive assistants, and agentic infrastructure.

**What you get:**

- One conversation, three channels — a unified transcript across SMS, email (with real `In-Reply-To` threading), and voice calls
- A policy engine that picks the channel (long-form → email, urgency → the contact's fastest channel, otherwise stay put), with agent override
- No-reply follow-ups with an SMS → email → voice escalation ladder
- Hard safety backstops below the agent: quiet hours per channel and timezone, per-contact rate limits, STOP/START/HELP consent handling (TCPA)
- A typed Python SDK (`ContactHopClient` + decorator-based `Agent`) — your LLM loop stays yours
- Production plumbing: API-key auth, Alembic migrations, delivery receipts, Docker/compose, CI

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design.

**Contents:** [Installation](#installation) · [Quickstart](#quickstart) · [Command center](#command-center) · [Python SDK](#python-sdk) · [HTTP API](#http-api-at-a-glance) · [Configuration](#configuration) · [Deploying to production](#deploying-to-production) · [Development](#development) · [License](#license)

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

### Docker

A one-command deployment with Postgres, running migrations before the API starts:

```bash
cp demo.env .env   # configure adapters, credentials, quiet hours, …
docker compose up
```

Or just the harness image: `docker build -t contacthop . && docker run -p 8000:8000 contacthop` (defaults to SQLite inside the container — fine for trying it out, use compose for anything real).

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

## Command center

Every deployment ships an operational dashboard at **`/dashboard`** — no separate frontend to deploy, no build step, no external assets. It shows contacts and conversations at a glance, and for each conversation the unified timeline: SMS, email, and voice turns interleaved with channel-switch, escalation, and consent events, plus live delivery status. You can also reply as the agent directly from the page (channel picked by the policy engine, or forced). Data flows through the same authenticated `/v1` API the SDK uses — enter your API key in the header field when one is configured. Auto-refreshes every 5 seconds; light and dark theme follow the system.

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

## HTTP API at a glance

Interactive OpenAPI docs live at `/docs` on a running server.

**Management API** (requires `Authorization: Bearer <key>` when `CONTACTHOP_API_KEYS` is set):

| Endpoint | Purpose |
|---|---|
| `POST /v1/contacts` | Create a contact with channel identities (phone, email) |
| `GET /v1/contacts/{id}` | Contact details, identities, consent state |
| `POST /v1/contacts/{id}/identities` | Attach another address (cross-channel identity resolution) |
| `GET /v1/contacts/{id}/stats` | Median reply latency per channel |
| `POST /v1/conversations` | Start a conversation (contact, goal, starting channel) |
| `GET /v1/conversations/{id}` | Status and current channel |
| `POST /v1/conversations/{id}/messages` | Send an agent reply (policy engine picks the channel unless specified) |
| `GET /v1/conversations/{id}/transcript` | Unified cross-channel transcript |
| `GET /v1/conversations/{id}/context` | Prompt-ready context: digest + recent messages verbatim |
| `GET /v1/conversations/{id}/events` | Channel switches, escalations, consent changes, notes |
| `POST /v1/conversations/{id}/switch` | Move the conversation to another channel explicitly |
| `POST /v1/conversations/{id}/call` | Originate a voice call (optional opening line) |
| `GET /v1/conversations/{id}/sessions` | Voice call session history |

**Webhooks** (provider-authenticated; never require Bearer keys):

| Endpoint | Caller |
|---|---|
| `POST /webhooks/twilio/sms` | Twilio — inbound SMS ("A message comes in") |
| `POST /webhooks/twilio/sms/status` | Twilio — delivery receipts (set automatically per message) |
| `POST /webhooks/twilio/voice/answer` / `turn` / `continue` / `status` | Twilio — call loop (URLs are passed at call origination) |
| `POST /webhooks/email/inbound` | Your email provider bridge — normalized inbound email JSON |

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
| `CONTACTHOP_SMS_HELP_REPLY` / `_SMS_OPT_IN_REPLY` | sensible defaults | Replies to the HELP and START SMS keywords |

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

### SMS consent (STOP/START/HELP)

Carrier-standard keywords are handled before the agent ever sees the message: **STOP** (and STOPALL/UNSUBSCRIBE/CANCEL/END/QUIT/REVOKE) marks the number opted out, **START** resubscribes with a confirmation, **HELP** gets an informational reply. Opt-out state lives on the channel identity (visible in the contact API) and is enforced in the gateway: explicit sends and calls to an opted-out number return 403, policy-routed messages hop to a channel the contact hasn't opted out of, and escalations skip it. The keyword message itself stays in the transcript for auditability, and the agent is notified via `conversation.contact.opt_out` / `.opt_in` events. Legally required for US texting (TCPA) — do not disable.

### Database migrations

Schema changes are managed with Alembic, and the migration scripts ship inside the package:

```bash
contacthop migrate            # upgrade to head (uses CONTACTHOP_DATABASE_URL)
contacthop migrate <revision> # upgrade/downgrade to a specific revision
```

For production, set `CONTACTHOP_AUTO_CREATE_TABLES=false` and run `contacthop migrate` as a deploy step. If you started on a dev database created by `auto_create_tables`, adopt migrations with `uv run alembic stamp head` once. New revisions are autogenerated from the repo: `uv run alembic revision --autogenerate -m "describe change"` — CI includes a drift guard that fails if models change without a matching migration.

## Deploying to production

The short version: run the compose stack behind an HTTPS reverse proxy, point Twilio's SMS webhook at it, bridge your email provider's inbound parse to the generic webhook, and point `CONTACTHOP_AGENT_WEBHOOK_URL` at your agent.

### 1. Stand up the harness

```bash
git clone https://github.com/contacthop/contacthop && cd contacthop
cp demo.env .env               # fill in everything below
docker compose up -d           # Postgres + migrations + API on :8000
```

Put an HTTPS reverse proxy (Caddy, nginx, or your platform's load balancer) in front of port 8000 — Twilio requires HTTPS webhook URLs, and `CONTACTHOP_PUBLIC_BASE_URL` must be that public HTTPS origin (e.g. `https://hop.example.com`). Without compose, the equivalent is: Postgres + `contacthop migrate` + `contacthop serve` with `CONTACTHOP_AUTO_CREATE_TABLES=false`.

Minimum production `.env`:

```bash
CONTACTHOP_PUBLIC_BASE_URL=https://hop.example.com
CONTACTHOP_API_KEYS=<long-random-token>
CONTACTHOP_AGENT_WEBHOOK_URL=https://your-agent.example.com/
POSTGRES_PASSWORD=<strong-password>          # used by docker-compose.yml
```

### 2. Wire up SMS + voice (Twilio)

```bash
CONTACTHOP_SMS_ADAPTER=twilio
CONTACTHOP_VOICE_ADAPTER=twilio
CONTACTHOP_TWILIO_ACCOUNT_SID=ACxxxxxxxx
CONTACTHOP_TWILIO_AUTH_TOKEN=xxxxxxxx
CONTACTHOP_TWILIO_FROM_NUMBER=+15550001234
```

Then in the [Twilio console](https://console.twilio.com) → Phone Numbers → your number → Messaging: set **"A message comes in"** to `POST https://hop.example.com/webhooks/twilio/sms`. That's the only console configuration needed — delivery-receipt and voice-call webhook URLs are passed programmatically per message/call. Webhook signatures are validated automatically once the auth token and `PUBLIC_BASE_URL` are set.

Note: voice is currently outbound-only (the agent originates calls); inbound calls to your number aren't handled yet.

### 3. Wire up email

Outbound goes through any SMTP relay (SES, SendGrid, Postmark, your own):

```bash
CONTACTHOP_EMAIL_ADAPTER=smtp
CONTACTHOP_SMTP_HOST=email-smtp.us-east-1.amazonaws.com
CONTACTHOP_SMTP_USERNAME=... CONTACTHOP_SMTP_PASSWORD=...
CONTACTHOP_SMTP_FROM_ADDRESS=assistant@example.com
CONTACTHOP_EMAIL_INBOUND_TOKEN=<random-token>
```

Inbound: point your provider's inbound-parse feature (SendGrid Inbound Parse, SES receipt rule → Lambda, Mailgun routes, Postmark inbound) at a tiny bridge that POSTs the normalized JSON to ContactHop:

```bash
curl -X POST https://hop.example.com/webhooks/email/inbound \
  -H "X-ContactHop-Token: $CONTACTHOP_EMAIL_INBOUND_TOKEN" \
  -H 'content-type: application/json' \
  -d '{
    "from_address": "human@example.com",
    "to_address": "assistant@example.com",
    "subject": "Re: quarterly report",
    "text": "Looks good, ship it.",
    "message_id": "<abc@mail.example.com>",
    "in_reply_to": "<xyz@example.com>"
  }'
```

The payload is provider-agnostic on purpose — the bridge is usually ~10 lines in a serverless function.

### 4. Connect your agent

Run an SDK `Agent` app (see [Python SDK](#python-sdk)) anywhere the harness can reach over HTTPS, and set `CONTACTHOP_AGENT_WEBHOOK_URL` to it. Every inbound message, due follow-up, delivery failure, and consent change arrives as a JSON `AgentNotification`; the agent replies through the management API with its Bearer key.

### 5. Pre-launch checklist

- [ ] `CONTACTHOP_API_KEYS` set (the API is otherwise open)
- [ ] Postgres + `CONTACTHOP_AUTO_CREATE_TABLES=false`, migrations run as a deploy step
- [ ] `PUBLIC_BASE_URL` is HTTPS and matches what Twilio calls (signature validation depends on it)
- [ ] Quiet hours (`SEND_WINDOW_*`) and `DEFAULT_TIMEZONE` reviewed for your audience
- [ ] Rate limit (`MAX_MESSAGES_PER_HOUR`) appropriate for your use case
- [ ] SMS consent replies (`SMS_HELP_REPLY`) identify who's texting — carriers require it
- [ ] Agent webhook is HTTPS and idempotent (notifications may retry)

**Local development against real providers:** expose your dev server with a tunnel (`ngrok http 8000`, `cloudflared tunnel`), set `CONTACTHOP_PUBLIC_BASE_URL` to the tunnel URL, and point the Twilio number's SMS webhook at it.

## Development

```bash
uv sync            # installs the package + dev group (pytest, ruff, mypy)
uv run pytest
uv run ruff check .
```

CI (GitHub Actions) runs ruff, the test suite on Python 3.11–3.13, a package build, and a Docker image build on every push and pull request.


## License

Apache License 2.0 — see [LICENSE](LICENSE). Copyright 2026 Collin Paran.
