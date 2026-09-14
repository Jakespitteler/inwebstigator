import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from app.core.config import config

FROM_ADDR: str = config.email

SMTP_HOST: str = config.smtp_host
SMTP_PORT: int = config.smtp_port
SMTP_USER: str = config.email
SMTP_PASS: str = config.email_password.get_secret_value()


def configuration_problems() -> list[str]:
    """What's stopping a real send, empty if it's good to go.

    Everything starts blank, so a half filled .env fails here instead of
    quietly mailing nobody.
    """
    problems: list[str] = []
    if not SMTP_USER:
        problems.append("EMAIL is empty -- set it to the account you're sending from")
    if not SMTP_PASS:
        problems.append("EMAIL_PASSWORD is empty -- Gmail needs an app password, not your login")
    if not SMTP_HOST:
        problems.append("SMTP_HOST is empty -- set it to your mail server (Gmail: smtp.gmail.com)")
    return problems


def _sender_domain() -> str | None:
    """Domain of FROM_ADDR, for the Message-ID."""
    _, address = parseaddr(FROM_ADDR)
    _, _, domain = address.partition("@")
    return domain or None


def build_message(
    subject: str,
    recipients: list[str],
    html_body: str | None = None,
    now: datetime | None = None,
) -> EmailMessage:
    """Put finished text in an email envelope. Doesn't send.

    Pass html_body and you get multipart/alternative, with body as the
    fallback. recipients defaults to CLIENT_TO.
    """
    now = now or datetime.now(UTC)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = FROM_ADDR
    msg["To"] = ", ".join(recipients)
    # Marks us as a bot, so no out-of-office replies come back.
    msg["Auto-Submitted"] = "auto-generated"
    # Python doesn't add either of these, and without them spam filters read
    # us as a bulk sender. Same reason the Message-ID domain matches the sender.
    msg["Date"] = formatdate(now.timestamp())
    msg["Message-ID"] = make_msgid(domain=_sender_domain())
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


def send_email(msg: EmailMessage) -> bool:
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
    except Exception as exc:
        print(f"  send failed: {exc}")
        return False
    return True
