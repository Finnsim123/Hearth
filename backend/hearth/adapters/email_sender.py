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
        from ..security import valid_email
        raw = [to] if isinstance(to, str) else list(to or [])
        recipients = [r.strip() for r in raw if r and valid_email(r)]
        dropped = len(raw) - len(recipients)
        if dropped:
            log.warning("email: dropped %d invalid recipient address(es)", dropped)
        if not recipients:
            log.warning("email send skipped — no valid recipients")
            return False

        # strip any CR/LF/control chars from header-bound values: an injected
        # newline in From/Subject is the classic header-injection vector.
        def _clean(v: str) -> str:
            return "".join(c for c in str(v) if c not in "\r\n\t\0")

        msg = EmailMessage()
        msg["From"] = formataddr((_clean(conf["from_name"]), _clean(conf["from_addr"])))
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = _clean(subject)
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
