"""Wraps finished text in an email and sends it.

Split out of notifier.py so the rendering side stays off the network.

  - the only file here that opens a socket
  - the only file here that reads the mail account settings
  - knows nothing about a Change
"""

import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from app.core.config import config

# From the shared Config. Constants so a test can override one.
CLIENT_TO = config.client_to
# We send as the account we log in as.
FROM_ADDR = config.email

# Opt in, so a fresh checkout can't accidentally mail anyone.
DRY_RUN = config.dry_run

SMTP_HOST = config.smtp_host
SMTP_PORT = config.smtp_port
SMTP_USER = config.email
SMTP_PASS = config.email_password.get_secret_value()


def configuration_problems() -> list[str]:
    """What's stopping a real send, empty if it's good to go.

    Everything starts blank, so a half filled .env fails here instead of
    quietly mailing nobody.
    """
    problems = []
    if DRY_RUN:
        problems.append("DRY_RUN is on -- set DRY_RUN=false in .env to send for real")
    if not SMTP_USER:
        problems.append("EMAIL is empty -- set it to the account you're sending from")
    if not SMTP_PASS:
        problems.append("EMAIL_PASSWORD is empty -- Gmail needs an app password, not your login")
    if not SMTP_HOST:
        problems.append("SMTP_HOST is empty -- set it to your mail server (Gmail: smtp.gmail.com)")
    if not CLIENT_TO:
        problems.append("CLIENT_TO is empty -- set it to the address the report should go to")
    return problems


def _sender_domain() -> str | None:
    """Domain of FROM_ADDR, for the Message-ID."""
    _, address = parseaddr(FROM_ADDR)
    _, _, domain = address.partition("@")
    return domain or None


def build_message(
    subject: str,
    body: str,
    html_body: str | None = None,
    now: datetime | None = None,
    recipients: list[str] | None = None,
) -> EmailMessage:
    """Put finished text in an email envelope. Doesn't send.

    Pass html_body and you get multipart/alternative, with body as the
    fallback. recipients defaults to CLIENT_TO.
    """
    now = now or datetime.now(UTC)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = FROM_ADDR
    msg["To"] = ", ".join(recipients or CLIENT_TO)
    # Marks us as a bot, so no out-of-office replies come back.
    msg["Auto-Submitted"] = "auto-generated"
    # Python doesn't add either of these, and without them spam filters read
    # us as a bulk sender. Same reason the Message-ID domain matches the sender.
    msg["Date"] = formatdate(now.timestamp())
    msg["Message-ID"] = make_msgid(domain=_sender_domain())
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


def send_email(msg: EmailMessage, dry_run: bool | None = None) -> bool:
    """Hand one message to the mail server. Returns whether it went out.

    dry_run=True just prints it, which is what DRY_RUN defaults to. Returns
    False instead of raising so a dead mail server can't take the run down.
    """
    if DRY_RUN if dry_run is None else dry_run:
        print("=" * 60)
        print(msg)
        return True

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
    except Exception as exc:
        print(f"  send failed: {exc}")
        return False
    return True
