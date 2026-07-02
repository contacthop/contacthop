"""SMTP email adapter using the stdlib (smtplib via a worker thread).

Sets RFC 5322 threading headers (Message-ID, In-Reply-To, References) so the
human's mail client keeps the conversation in one thread.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

from contacthop.channels.base import ChannelSendError, ProviderReceipt
from contacthop.domain.enums import ChannelType


class SMTPEmailAdapter:
    channel = ChannelType.EMAIL

    def __init__(
        self,
        host: str,
        port: int,
        from_address: str,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.from_address = from_address
        self.username = username
        self.password = password
        self.starttls = starttls

    async def send(
        self, to_address: str, body: str, meta: dict[str, Any] | None = None
    ) -> ProviderReceipt:
        meta = meta or {}
        subject = meta.get("subject") or "(no subject)"
        message_id = make_msgid(domain=self.from_address.split("@")[-1])

        msg = EmailMessage()
        msg["From"] = self.from_address
        msg["To"] = to_address
        msg["Subject"] = subject
        msg["Message-ID"] = message_id
        references: list[str] = list(meta.get("references") or [])
        if meta.get("in_reply_to"):
            msg["In-Reply-To"] = meta["in_reply_to"]
            if meta["in_reply_to"] not in references:
                references.append(meta["in_reply_to"])
        if references:
            msg["References"] = " ".join(references)
        msg.set_content(body)

        try:
            await asyncio.to_thread(self._send_sync, msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise ChannelSendError(f"SMTP send failed: {exc}") from exc

        return ProviderReceipt(
            provider_message_id=message_id,
            meta={
                "adapter": "smtp",
                "subject": subject,
                "in_reply_to": meta.get("in_reply_to"),
                "references": references,
            },
        )

    def _send_sync(self, msg: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=30) as server:
            if self.starttls:
                server.starttls()
            if self.username and self.password:
                server.login(self.username, self.password)
            server.send_message(msg)
