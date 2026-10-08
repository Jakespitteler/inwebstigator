import asyncio
from collections.abc import Sequence

from app.backend.email_service.delivery import EmailSender, confirm_address_can_receive_email, send_confirmation
from app.backend.email_service.message_builder import OutgoingEmail
from app.core.errors import NotFoundError
from app.db.services.recipient_service import RecipientService
from app.db.session import db_context


def _is_known_recipient(email: str) -> bool:
    """Checks whether an email address is already a recipient, so it has already been confirmed.

    Args:
        email: The email address to look for.

    Returns:
        True if the address is already a recipient.
    """
    with db_context() as session:
        try:
            RecipientService(session).get_by_email(email)
        except NotFoundError:
            return False
    return True


async def confirm_addresses_can_receive_email(
    emails: Sequence[str], subject: str, html_body: str, email_sender: EmailSender
) -> None:
    """Emails each address to confirm it, so an address that bounces is not added.

    An address that is already a recipient has been confirmed before, so it is emailed without waiting for a bounce.
    Each address is only emailed once, even if it was given twice. The emails are sent from threads, so the dashboard
    keeps responding while they send.

    Args:
        emails: The email addresses being added.
        subject: The subject of the email.
        html_body: The HTML content of the email.
        email_sender: Sends the emails.

    Raises:
        UndeliverableEmailError: If an email to an address could not be delivered.
    """
    await asyncio.gather(
        *(
            asyncio.to_thread(
                send_confirmation if _is_known_recipient(email) else confirm_address_can_receive_email,
                OutgoingEmail(to=email, subject=subject, html_body=html_body),
                email_sender,
            )
            for email in dict.fromkeys(emails)
        )
    )
