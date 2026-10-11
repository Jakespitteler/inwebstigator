import logging
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from app.backend.email_service.delivery import EmailSender
from app.backend.email_service.message_builder import OutgoingEmail
from app.db.services.recipient_service import RecipientService
from app.db.session import db_context
from app.models.recipient_models import RecipientRead

logger: logging.Logger = logging.getLogger(__name__)


def group_by_recipient[T](
    items: Iterable[T], recipients_of: Callable[[T], Iterable[RecipientRead]]
) -> dict[str, list[T]]:
    """Groups items, such as websites or their reports, by the email address of each recipient they are for.

    Args:
        items: The items to group.
        recipients_of: Gets the recipients an item is for.

    Returns:
        Each recipient's items, by email address, in the order they were given.
    """
    items_by_recipient: defaultdict[str, list[T]] = defaultdict(list)
    for item in items:
        for recipient in recipients_of(item):
            items_by_recipient[recipient.email].append(item)
    return dict(items_by_recipient)


def _record_email_sent(recipient_email: str) -> None:
    """Records that a recipient was just emailed, so their health checks count from now.

    Args:
        recipient_email: The recipient's email address.
    """
    with db_context() as session:
        RecipientService(session).record_emailed([recipient_email], emailed_at=datetime.now(UTC))


def send_notification(email: OutgoingEmail, email_sender: EmailSender) -> None:
    """Emails a recipient, then records when they were emailed.

    The email has already gone by the time it is recorded, so a failure to record it is logged rather than
    raised, and is not mistaken for the email failing to send.

    Args:
        email: The recipient, subject and HTML body.
        email_sender: Sends the email.

    Raises:
        smtplib.SMTPException: If the email could not be sent.
        OSError: If the mail server could not be reached.
    """
    email_sender.send(email)
    try:
        _record_email_sent(email.to)
    except Exception:
        logger.exception("%s was emailed, but when could not be recorded, so a health check may come early.", email.to)


def send_notifications(emails: Iterable[OutgoingEmail], email_sender: EmailSender) -> None:
    """Sends each email over one connection to the mail server, stopping at the first one that fails.

    Blocks while the emails send, so async code runs it in a thread.

    Args:
        emails: The emails to send.
        email_sender: Sends the emails.

    Raises:
        smtplib.SMTPException: If an email could not be sent. The emails after it are not sent.
        OSError: If the mail server could not be reached.
    """
    with email_sender:
        for email in emails:
            send_notification(email, email_sender)


def send_notifications_skipping_failures(
    emails: Iterable[OutgoingEmail], email_sender: EmailSender
) -> dict[OutgoingEmail, Exception]:
    """Sends each email over one connection to the mail server, logging any that fail, so one bad address does not
    stop the other emails being sent.

    Blocks while the emails send, so async code runs it in a thread.

    Args:
        emails: The emails to send.
        email_sender: Sends the emails.

    Returns:
        The emails that could not be sent, with why, so a caller can try them again later.
    """
    failures: dict[OutgoingEmail, Exception] = {}
    with email_sender:
        for email in emails:
            try:
                send_notification(email, email_sender)
            except Exception as error:
                logger.exception("Failed to send %r to %s", email.subject, email.to)
                failures[email] = error
    return failures
