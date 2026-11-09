"""Udgående e-mail via SMTP (SMTP_* i .env). Bruges til bekræftelseskoder (services/konto.py).

Uden SMTP_HOST sendes intet: beskeden skrives i loggen, så oprettelse kan afprøves under udvikling. smtplib er
blokerende og kører derfor i en tråd.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from portal.config import get_settings

log = logging.getLogger("portal.mail")
TIMEOUT = 20


class MailError(Exception):
    pass


def configured() -> bool:
    return bool(get_settings().smtp_host)


def _sender() -> str:
    s = get_settings()
    return s.smtp_from or (formataddr((s.app_name, s.smtp_user)) if s.smtp_user else "")


def _send_sync(msg: EmailMessage) -> None:
    s = get_settings()
    ctx = ssl.create_default_context()
    if s.smtp_security == "ssl":
        smtp = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=TIMEOUT, context=ctx)
    else:
        smtp = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=TIMEOUT)
    with smtp:
        if s.smtp_security == "starttls":
            smtp.starttls(context=ctx)
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


async def send(to: str, subject: str, text: str) -> None:
    """Send en ren tekst-mail. MailError, hvis SMTP-serveren afviser eller ikke svarer."""
    if not configured():
        log.warning("SMTP er ikke sat op – mailen til %s sendes ikke:\n%s\n%s", to, subject, text)
        return
    msg = EmailMessage()
    sender = _sender()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1].strip("> ") or None)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(text)
    try:
        await asyncio.to_thread(_send_sync, msg)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("Mail til %s kunne ikke sendes: %s", to, exc)
        raise MailError(str(exc)) from exc
