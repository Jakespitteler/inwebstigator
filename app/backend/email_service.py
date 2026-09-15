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


def _sender_domain() -> str | None:
    """Extracts the domain portion of the configured sender email address.

    Parses FROM_ADDR to isolate the domain following the '@' symbol, which is used
    to construct compliant Message-ID headers.

    Returns:
        The domain string if successfully parsed, otherwise None.
    """
    _, address = parseaddr(FROM_ADDR)
    _, _, domain = address.partition("@")
    return domain or None


def build_message(
    subject: str,
    recipients: list[str],
    html_body: str,
    now: datetime | None = None,
) -> EmailMessage:
    """Constructs a structured MIME EmailMessage instance without sending it.

    Populates standard metadata headers (Subject, From, To, Auto-Submitted, Date,
    Message-ID) and attaches the HTML body payload.

    Args:
        subject: The subject text line for the email message.
        recipients: A list of recipient email address strings.
        html_body: The raw HTML content string representing the email body.
        now: Optional datetime object representing the sending timestamp.
            Defaults to current UTC time if omitted.

    Returns:
        A fully constructed EmailMessage instance ready for transmission.
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
    msg.add_alternative(html_body, subtype="html")
    return msg


def send_email(msg: EmailMessage) -> bool:
    """Transmits a constructed EmailMessage object over an encrypted SMTP_SSL connection.

    Establishes an SSL connection to the configured SMTP host and port, handles
    authentication if required, and dispatches the email payload.

    Args:
        msg: The prepared EmailMessage instance to send.

    Returns:
        True if the email was successfully accepted by the SMTP server, False if
        an exception or connection failure occurred.
    """
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
    except Exception as exc:
        print(f"  send failed: {exc}")
        return False
    return True
