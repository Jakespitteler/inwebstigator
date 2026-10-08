import asyncio
from collections.abc import Sequence
from datetime import datetime, time, timedelta

from app.backend.email_service.delivery import EmailSender
from app.backend.email_service.email_wording import WebsiteHealth, describe_website_health, health_check_subject
from app.backend.email_service.html_bodies import health_check_html
from app.backend.email_service.message_builder import OutgoingEmail
from app.db.services.recipient_service import RecipientService
from app.db.services.website_service import WebsiteService
from app.db.session import db_context
from app.models.recipient_models import RecipientRead
from app.models.website_models import WebsiteRead
from app.scanning.notifications import group_by_recipient, send_notifications_skipping_failures


def is_health_check_due(recipient: RecipientRead, now: datetime) -> bool:
    """Checks whether a recipient has gone long enough without an email to be sent a health check.

    The threshold counts from the start of the day they were last emailed. Emails go out partway
    through a daily run, so counting from the exact time would make the next due run look slightly
    short of the threshold and delay the health check by a whole day.

    Args:
        recipient: The recipient to check.
        now: The time of the check.

    Returns:
        True if the recipient has been emailed before and the threshold has passed since.
    """
    if recipient.last_email_at is None:
        return False
    start_of_last_email_day: datetime = datetime.combine(recipient.last_email_at.date(), time.min)
    return now - start_of_last_email_day >= timedelta(days=recipient.days_between_health_checks)


def _health_check_email(recipient_email: str, websites: Sequence[WebsiteRead], now: datetime) -> OutgoingEmail:
    """Writes a recipient's health check, which says how each of their websites is doing, so it does not say all is
    well while scans are failing or a website is switched off.

    Args:
        recipient_email: Who the health check is for.
        websites: The recipient's websites, as saved after the latest scan.
        now: When the health check is sent.

    Returns:
        The health check email.
    """
    website_healths: list[WebsiteHealth] = [describe_website_health(website, now) for website in websites]
    return OutgoingEmail(
        to=recipient_email,
        subject=health_check_subject(website_healths),
        html_body=health_check_html(website_healths),
    )


async def send_due_health_checks(email_sender: EmailSender) -> None:
    """Sends a health check to each recipient who has gone long enough without an email.

    Recipients are read every time, so an email sent by a scan that has just run counts as recent contact, and newly
    added recipients and changed intervals are picked up. Only recipients still linked to a website are sent one.
    The emails are sent from a thread, and a failed email is logged, so one bad address does not stop the others.

    Args:
        email_sender: Sends the emails.
    """
    with db_context() as session:
        recipients: Sequence[RecipientRead] = RecipientService(session).get_all_with_websites()
        websites: Sequence[WebsiteRead] = WebsiteService(session).get_all(limit=None)

    now: datetime = datetime.now()
    websites_by_recipient: dict[str, list[WebsiteRead]] = group_by_recipient(
        websites, lambda website: website.recipients
    )
    health_checks: list[OutgoingEmail] = [
        _health_check_email(recipient.email, websites_by_recipient.get(recipient.email, []), now)
        for recipient in recipients
        if is_health_check_due(recipient, now)
    ]
    await asyncio.to_thread(send_notifications_skipping_failures, health_checks, email_sender)
