"""Email sender — authenticated SMTP relay (Gmail-friendly).

This is NOT a mail server: no inbound, no port 25, no IP reputation of our own.
We just authenticate to an existing provider's SMTP (Gmail app-password, Fastmail,
SES, …) and hand off one message. Keeps the 'light to run' story — one outbound
TLS connection, no extra container — and sidesteps the deliverability/abuse traps
of self-hosting MTA software.

Creds live encrypted as the 'smtp' connection (token = the password, via
HEARTH_SECRET). Used by the weekly newsletter and by auth recovery mail.
smtplib is blocking; async callers (the scheduler) should use asyncio.to_thread,
and FastAPI sync endpoints already run in a threadpool.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger(__name__)


class EmailSender:
    """Implements the outbound side of the Notifier story for email."""

    def __init__(self, repo) -> None:
        self.repo = repo

    def configured(self) -> bool:
        return self._conf() is not None

    def _conf(self) -> dict | None:
        c = self.repo.get_connection("smtp")
        if not c or not c.get("url"):
            return None
        o = c.get("options") or {}
        user = o.get("username", "")
        return {
            "host": c["url"],
            "port": int(o.get("port", 587)),
            "user": user,
            "password": c.get("token", ""),
            "from_addr": o.get("from") or user,
            "from_name": o.get("from_name", "Hearth"),
            "tls": o.get("tls", "starttls"),   # starttls | ssl | none
        }

    def send(self, to: str | list[str], subject: str, html: str,
             text: str | None = None) -> bool:
        """Send one multipart (text + HTML) message. Returns True on success;
        never raises — callers treat email as best-effort."""
        conf = self._conf()
        if not conf:
            log.warning("email send skipped — SMTP not configured")
            return False
        recipients = [to] if isinstance(to, str) else [r for r in to if r]
        if not recipients:
            return False

        msg = EmailMessage()
        msg["From"] = formataddr((conf["from_name"], conf["from_addr"]))
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.set_content(text or "This message needs an HTML-capable mail client.")
        msg.add_alternative(html, subtype="html")

        try:
            ctx = ssl.create_default_context()
            if conf["tls"] == "ssl":
                with smtplib.SMTP_SSL(conf["host"], conf["port"], context=ctx, timeout=20) as s:
                    self._auth_send(s, conf, msg)
            else:
                with smtplib.SMTP(conf["host"], conf["port"], timeout=20) as s:
                    s.ehlo()
                    if conf["tls"] != "none":
                        s.starttls(context=ctx)
                        s.ehlo()
                    self._auth_send(s, conf, msg)
            log.info("email sent to %d recipient(s): %s", len(recipients), subject)
            return True
        except Exception as exc:
            log.warning("email send failed: %s", exc)
            return False

    @staticmethod
    def _auth_send(s: smtplib.SMTP, conf: dict, msg: EmailMessage) -> None:
        if conf["user"]:
            s.login(conf["user"], conf["password"])
        s.send_message(msg)
