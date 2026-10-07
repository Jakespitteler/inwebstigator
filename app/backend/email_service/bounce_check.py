import imaplib
import logging
import ssl
import time

from app.backend.email_service.message_builder import OutgoingEmail
from app.backend.email_service.sender import EmailSender, send_confirmation
from app.backend.email_service.settings import InboxSettings
from app.core.errors import UndeliverableEmailError

BOUNCE_SENDERS_SEARCH: str = '(OR FROM "mailer-daemon" FROM "postmaster")'

logger: logging.Logger = logging.getLogger(__name__)


def _open_inbox(settings: InboxSettings) -> imaplib.IMAP4_SSL | None:
    """Logs in to the inbox of the account that sends the emails, which is where bounced emails are returned.

    The server's certificate is checked, so the password cannot be sent to a server pretending to be the real one.

    Args:
        settings: The inbox server and login.

    Returns:
        The logged in connection, or None if the inbox could not be reached.
    """
    if settings.host is None:
        logger.warning("No inbox (IMAP) server is set and it cannot be worked out, so bounces cannot be detected.")
        return None
    try:
        inbox = imaplib.IMAP4_SSL(
            host=settings.host,
            port=settings.port,
            ssl_context=ssl.create_default_context(),
            timeout=settings.timeout_seconds,
        )
        inbox.login(user=settings.username, password=settings.password.get_secret_value())
    except (imaplib.IMAP4.error, OSError):
        logger.warning("Could not open the inbox at %s, so bounced emails cannot be detected.", settings.host)
        return None
    return inbox


def _count_bounces(inbox: imaplib.IMAP4_SSL, address: str) -> int:
    """Counts the delivery failure emails in the inbox that mention an address.

    Gmail and most other providers send them from "mailer-daemon", and Microsoft 365 and Outlook from "postmaster".

    Args:
        inbox: A logged in connection to the inbox.
        address: The email address to look for. It must already be validated, as it is placed in the search.

    Returns:
        The number of delivery failure emails that mention the address.
    """
    inbox.select(mailbox="INBOX", readonly=True)
    _, matches = inbox.search(None, f'({BOUNCE_SENDERS_SEARCH} TEXT "{address}")')
    return len((matches[0] or b"").split())


def _wait_for_bounce(inbox: imaplib.IMAP4_SSL, address: str, bounces_before: int, settings: InboxSettings) -> bool:
    """Waits for a delivery failure email about an address to arrive in the inbox.

    Args:
        inbox: A logged in connection to the inbox.
        address: The email address that was emailed.
        bounces_before: How many delivery failure emails already mentioned the address before it was emailed.
        settings: How long to wait, and how often to check.

    Returns:
        True if a new delivery failure email arrived before the wait ran out, otherwise False.
    """
    for _ in range(settings.bounce_wait_seconds // settings.bounce_poll_seconds):
        time.sleep(settings.bounce_poll_seconds)
        if _count_bounces(inbox, address) > bounces_before:
            return True
    return False


def confirm_address_can_receive_email(
    email: OutgoingEmail,
    email_sender: EmailSender,
    settings: InboxSettings | None = None,
) -> None:
    """Emails an address, then waits to see whether the email bounces.

    A mail server such as Gmail accepts an email first and only reports a missing mailbox afterwards, by sending
    a delivery failure email back to the sending account's inbox. So this watches that inbox for a short time.
    A bounce that arrives after the wait is not seen. If the inbox cannot be reached, the email is sent
    but its bounce cannot be watched for.

    Args:
        email: The confirmation email, addressed to the address being confirmed.
        email_sender: Sends the email.
        settings: The inbox to watch, and for how long. Defaults to the app's configured settings.

    Raises:
        UndeliverableEmailError: If the email is refused or bounces.
    """
    inbox_settings: InboxSettings = settings or InboxSettings.from_config()
    inbox: imaplib.IMAP4_SSL | None = _open_inbox(inbox_settings)
    if inbox is None:
        send_confirmation(email, email_sender)
        return

    with inbox:
        bounces_before: int = _count_bounces(inbox, email.to)
        send_confirmation(email, email_sender)
        if _wait_for_bounce(inbox, address=email.to, bounces_before=bounces_before, settings=inbox_settings):
            raise UndeliverableEmailError(email.to)
