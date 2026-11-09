"""Udgående e-mail via SMTP (SMTP_* i .env). Bruges til bekræftelseskoder (services/konto.py).

Uden SMTP_HOST sendes intet: beskeden skrives i loggen, så oprettelse kan afprøves under udvikling. smtplib er
blokerende og kører derfor i en tråd.

Mails sendes som tekst + HTML (layout()). HTML'en følger portalens udseende – grøn brand-farve, beige baggrund, hvidt kort
og logoet – men er bygget med tabeller og inline-styles, som e-mailprogrammer (Outlook, Gmail, Apple Mail) kræver.
"""

from __future__ import annotations

import asyncio
import html
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


# Portalens farver (web/css/app.css, lyst tema). Mails vises altid lyst, så koden og knappen står tydeligt.
BRAND, BRAND_SOFT, BG, SURFACE, LINE, TEXT, TEXT_2, TEXT_3 = (
    "#1f5a44", "#e1eee7", "#f4f2ec", "#ffffff", "#dcd8cc", "#1d2420", "#4c5650", "#7b847e")
FONT = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def layout(*, title: str, preheader: str, greeting: str, paragraphs: list[str], code: str | None = None,
           button: tuple[str, str] | None = None, notes: list[str] | None = None) -> str:
    """HTML-mail i portalens stil. Al tekst escapes; afsnit kan indeholde **fed** tekst."""
    s = get_settings()
    base = s.public_url.rstrip("/")
    host = base.split("://", 1)[-1]

    def esc(t: str) -> str:
        parts = html.escape(t).split("**")
        return "".join(f"<strong style=\"color:{TEXT};\">{p}</strong>" if i % 2 else p for i, p in enumerate(parts))

    p_style = f"margin:0 0 16px;font-size:15px;line-height:1.6;color:{TEXT_2};"
    body = [f'<p style="{p_style}color:{TEXT};">{esc(greeting)}</p>']
    body += [f'<p style="{p_style}">{esc(t)}</p>' for t in paragraphs]
    if code:
        body.append(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 24px;"><tr>'
            f'<td align="center" style="background:{BRAND_SOFT};border:1px solid #c9e0d3;border-radius:12px;padding:20px 12px;">'
            f'<div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:{BRAND};font-weight:700;'
            f'margin-bottom:6px;">Din kode</div>'
            f'<div style="font-family:\'SF Mono\',Menlo,Consolas,monospace;font-size:34px;font-weight:700;letter-spacing:.32em;'
            f'color:{TEXT};padding-left:.32em;">{html.escape(code)}</div></td></tr></table>')
    if button:
        label, url = button
        body.append(
            f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 24px;"><tr>'
            f'<td style="border-radius:10px;background:{BRAND};">'
            f'<a href="{html.escape(url, quote=True)}" style="display:inline-block;padding:13px 26px;font-size:15px;font-weight:700;'
            f'color:#ffffff;text-decoration:none;border-radius:10px;">{html.escape(label)}</a></td></tr></table>')
    for n in notes or []:
        body.append(f'<p style="margin:0 0 10px;font-size:13px;line-height:1.55;color:{TEXT_3};">{esc(n)}</p>')

    return f"""<!doctype html>
<html lang="da"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><meta name="supported-color-schemes" content="light">
<title>{html.escape(title)}</title></head>
<body style="margin:0;padding:0;background:{BG};-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{BG};">{html.escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};font-family:{FONT};">
<tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;">
    <tr><td style="padding:0 4px 18px;">
      <table role="presentation" cellpadding="0" cellspacing="0"><tr>
        <td style="vertical-align:middle;"><img src="{base}/icons/icon-192.png" width="40" height="40" alt=""
          style="display:block;border:0;border-radius:11px;"></td>
        <td style="vertical-align:middle;padding-left:12px;">
          <div style="font-size:18px;font-weight:800;color:{TEXT};line-height:1.1;">{html.escape(s.app_name)}</div>
          <div style="font-size:12px;color:{TEXT_3};line-height:1.3;">feltornitologernes portal</div></td>
      </tr></table></td></tr>
    <tr><td style="background:{SURFACE};border:1px solid {LINE};border-radius:16px;overflow:hidden;">
      <div style="height:5px;background:{BRAND};font-size:0;line-height:0;">&nbsp;</div>
      <div style="padding:32px 32px 20px;">
        <h1 style="margin:0 0 20px;font-size:22px;line-height:1.3;font-weight:800;color:{TEXT};">{html.escape(title)}</h1>
        {''.join(body)}
      </div></td></tr>
    <tr><td style="padding:20px 8px 0;font-size:12px;line-height:1.6;color:{TEXT_3};text-align:center;">
      Denne mail er sendt automatisk fra {html.escape(s.app_name)} og kan ikke besvares.<br>
      <a href="{base}/" style="color:{BRAND};text-decoration:none;">{html.escape(host)}</a> ·
      <a href="{base}/privatliv" style="color:{BRAND};text-decoration:none;">Privatlivspolitik</a></td></tr>
  </table>
</td></tr></table>
</body></html>"""


async def send(to: str, subject: str, text: str, html_body: str | None = None) -> None:
    """Send en mail som tekst (og HTML, hvis givet). MailError, hvis SMTP-serveren afviser eller ikke svarer."""
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
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    try:
        await asyncio.to_thread(_send_sync, msg)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("Mail til %s kunne ikke sendes: %s", to, exc)
        raise MailError(str(exc)) from exc
