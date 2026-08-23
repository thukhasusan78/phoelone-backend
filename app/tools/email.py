from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Any

from app.config import Settings


class EmailTool:
    name = "send_email"
    declaration = {
        "name": "send_email",
        "description": (
            "Send a short email. Requires SMTP on the server. "
            "If the tool returns configured=false, tell the user email is not set up yet."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Subject line"},
                "body": {"type": "string", "description": "Plain-text body"},
            },
            "required": ["to", "subject", "body"],
        },
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def configured(self) -> bool:
        return bool(self.settings.smtp_host and self.settings.smtp_from)

    async def __call__(
        self,
        to: str,
        subject: str,
        body: str,
        **_: Any,
    ) -> dict[str, Any]:
        to = (to or "").strip()
        subject = (subject or "").strip()
        body = (body or "").strip()
        if not to or "@" not in to:
            return {"error": "invalid recipient", "configured": self.configured()}
        if not subject or not body:
            return {"error": "subject and body are required", "configured": self.configured()}
        if not self.configured():
            return {
                "error": "email is not configured",
                "configured": False,
            }
        await asyncio.to_thread(self._send, to, subject, body)
        return {"ok": True, "to": to, "configured": True}

    def _send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        if self.settings.smtp_use_tls:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                if self.settings.smtp_user:
                    smtp.login(self.settings.smtp_user, self.settings.smtp_password)
                smtp.send_message(message)
            return
        with smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as smtp:
            if self.settings.smtp_user:
                smtp.login(self.settings.smtp_user, self.settings.smtp_password)
            smtp.send_message(message)
