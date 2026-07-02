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

## Configuration

Settings come from environment variables (or a `.env` file), prefixed with `CONTACTHOP_`:

| Variable | Default | Purpose |
|---|---|---|
| `CONTACTHOP_DATABASE_URL` | `sqlite+aiosqlite:///contacthop.db` | Any async SQLAlchemy URL (use `postgresql+asyncpg://…` in production) |
| `CONTACTHOP_SMS_ADAPTER` | `console` | `console` or `twilio` |
| `CONTACTHOP_EMAIL_ADAPTER` | `console` | `none`, `console`, or `smtp` |
| `CONTACTHOP_TWILIO_ACCOUNT_SID` / `_AUTH_TOKEN` / `_FROM_NUMBER` | — | Required for the Twilio adapter |
| `CONTACTHOP_SMTP_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` / `_FROM_ADDRESS` / `_STARTTLS` | — | Required for the SMTP adapter (host + from address at minimum) |
| `CONTACTHOP_EMAIL_INBOUND_TOKEN` | — | Shared secret (`X-ContactHop-Token`) guarding the inbound email webhook |
| `CONTACTHOP_PUBLIC_BASE_URL` | — | Public URL of this deployment, used for webhook signature validation |
| `CONTACTHOP_AGENT_WEBHOOK_URL` | — | Where conversation events are pushed for your agent |
| `CONTACTHOP_FOLLOW_UP_POLL_INTERVAL` | `5.0` | Seconds between scheduler sweeps for due follow-ups |

## Development

```bash
uv sync            # installs the package + dev group (pytest, ruff, mypy)
uv run pytest
uv run ruff check .
```

