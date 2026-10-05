import imaplib
import logging
import smtplib
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from tenacity import retry, retry_if_exception_type, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from app.core.config import config
from app.core.errors import UndeliverableEmailError

FROM_ADDR: str = config.email

SMTP_HOST: str = config.smtp_host
SMTP_PORT: int = config.smtp_port
SMTP_TIMEOUT_SECONDS: int = config.smtp_timeout_seconds
SMTP_USER: str = config.email
SMTP_PASS: str = config.email_password.get_secret_value()

IMAP_HOST: str = config.imap_host or SMTP_HOST.replace("smtp.", "imap.", 1)
IMAP_PORT: int = config.imap_port
EMAIL_BOUNCE_WAIT_SECONDS: int = config.email_bounce_wait_seconds
EMAIL_BOUNCE_POLL_SECONDS: int = config.email_bounce_poll_seconds


logger: logging.Logger = logging.getLogger(__name__)


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


# A wrong password or a refused address will fail the same way every time, so is not retried
PERMANENT_SMTP_ERRORS = (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)


@retry(
    wait=wait_exponential(
        multiplier=config.email_retry_multiplier,
        min=config.email_retry_min_wait_seconds,
        max=config.email_retry_max_wait_seconds,
    ),
    stop=stop_after_attempt(config.email_retry_max_attempts),
    retry=retry_if_exception_type((smtplib.SMTPException, TimeoutError, ConnectionError))
    & retry_if_not_exception_type(PERMANENT_SMTP_ERRORS),
    reraise=True,
)
def send_email(msg: EmailMessage) -> None:
    """Transmits a constructed EmailMessage object over an encrypted SMTP_SSL connection.

    Establishes an SSL connection to the configured SMTP host and port, handles
    authentication if required, and dispatches the email payload. Applies automatic
    retry logic with exponential backoff for transient network or SMTP errors. Permanent SMTP errors
    (a failed login, or a refused sender or recipient) are raised straight away.

    Args:
        msg: The prepared EmailMessage instance to send.

    Raises:
        smtplib.SMTPException: If an SMTP-related error occurs and all retry attempts are exhausted.
        TimeoutError: If the connection times out and all retry attempts are exhausted.
        ConnectionError: If a network connection failure occurs and all retry attempts are exhausted.
    """
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        if SMTP_USER:
            smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)


def _open_inbox() -> imaplib.IMAP4_SSL | None:
    """Logs in to the inbox of the account that sends the emails, which is where bounced emails are returned.

    Returns:
        The logged in connection, or None if the inbox could not be reached.
    """
    try:
        inbox = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=SMTP_TIMEOUT_SECONDS)
        inbox.login(SMTP_USER, SMTP_PASS)
    except (imaplib.IMAP4.error, OSError):
        logger.warning(f"Could not open the inbox at {IMAP_HOST}, so bounced emails cannot be detected.")
        return None
    return inbox


def _count_bounces(inbox: imaplib.IMAP4_SSL, address: str) -> int:
    """Counts the delivery failure emails in the inbox that mention an address.

    Args:
        inbox: A logged in connection to the inbox.
        address: The email address to look for. It must already be validated, as it is placed in the search.

    Returns:
        The number of delivery failure emails that mention the address.
    """
    inbox.select("INBOX", readonly=True)
    _, matches = inbox.search(None, f'(FROM "mailer-daemon" TEXT "{address}")')
    return len((matches[0] or b"").split())


def send_confirmation(address: str, subject: str, html_body: str) -> None:
    """Sends an email to one address.

    Args:
        address: The email address to send to.
        subject: The subject text line for the email.
        html_body: The HTML content of the email.

    Raises:
        UndeliverableEmailError: If the mail server refuses the address straight away.
    """
    try:
        send_email(build_message(subject=subject, recipients=[address], html_body=html_body))
    except smtplib.SMTPRecipientsRefused as error:
        raise UndeliverableEmailError(address) from error


def _wait_for_bounce(inbox: imaplib.IMAP4_SSL, address: str, bounces_before: int) -> bool:
    """Waits for a delivery failure email about an address to arrive in the inbox.

    Args:
        inbox: A logged in connection to the inbox.
        address: The email address that was emailed.
        bounces_before: How many delivery failure emails already mentioned the address before it was emailed.

    Returns:
        True if a new delivery failure email arrived before the wait ran out, otherwise False.
    """
    for _ in range(EMAIL_BOUNCE_WAIT_SECONDS // EMAIL_BOUNCE_POLL_SECONDS):
        time.sleep(EMAIL_BOUNCE_POLL_SECONDS)
        if _count_bounces(inbox, address) > bounces_before:
            return True
    return False


def confirm_address_can_receive_email(address: str, subject: str, html_body: str) -> None:
    """Emails an address, then waits to see whether the email bounces.

    A mail server such as Gmail accepts an email first and only reports a missing mailbox afterwards, by sending
    a delivery failure email back to the sending account's inbox. So this watches that inbox for a short time.
    A bounce that arrives after the wait is not seen. If the inbox cannot be reached, the email is sent
    but its bounce cannot be watched for.

    Args:
        address: The email address to confirm.
        subject: The subject text line for the confirmation email.
        html_body: The HTML content of the confirmation email.

    Raises:
        UndeliverableEmailError: If the email is refused or bounces.
    """
    inbox: imaplib.IMAP4_SSL | None = _open_inbox()
    if inbox is None:
        send_confirmation(address, subject, html_body)
        return

    with inbox:
        bounces_before: int = _count_bounces(inbox, address)
        send_confirmation(address, subject, html_body)
        if _wait_for_bounce(inbox, address, bounces_before):
            raise UndeliverableEmailError(address)
