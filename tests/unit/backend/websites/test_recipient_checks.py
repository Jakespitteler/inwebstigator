import asyncio
import smtplib
import threading

import pytest
from sqlalchemy.orm import Session

from app.backend.email_service import delivery
from app.backend.email_service.message_builder import OutgoingEmail
from app.backend.websites import recipient_checks
from app.backend.websites.recipient_checks import confirm_addresses_can_receive_email
from app.core.errors import UndeliverableEmailError
from app.db.schema import DBRecipient
from tests.fakes import FakeEmailSender

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("backend_uses_test_session")]

SUBJECT: str = "Website monitoring started"
HTML_BODY: str = "<p>Now monitoring https://example.com/</p>"


@pytest.fixture
def bouncing_addresses(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Undoes the autouse fixture that skips confirmations, so the emails are really sent through the fake sender.

    Watching a real inbox for bounces is replaced by a stand-in: an address in the returned set bounces once it has
    been emailed, which only the bounce-watching confirmation notices.

    Returns:
        The addresses that bounce. Add to it to make an address bounce.
    """
    bouncing: set[str] = set()

    def confirm_and_watch_for_bounce(email: OutgoingEmail, email_sender: delivery.EmailSender) -> None:
        delivery.send_confirmation(email, email_sender)
        if email.to in bouncing:
            raise UndeliverableEmailError(email.to)

    monkeypatch.setattr(recipient_checks, "send_confirmation", delivery.send_confirmation)
    monkeypatch.setattr(recipient_checks, "confirm_address_can_receive_email", confirm_and_watch_for_bounce)
    return bouncing


class RefusingEmailSender(FakeEmailSender):
    """A fake mail server that refuses one address straight away, as it would for a mailbox it knows is missing."""

    def __init__(self, refused_address: str) -> None:
        super().__init__()
        self.refused_address: str = refused_address

    def send(self, email: OutgoingEmail) -> None:
        """Refuses the email if it is to the refused address, otherwise records it as sent.

        Args:
            email: The email.

        Raises:
            smtplib.SMTPRecipientsRefused: If the email is to the refused address.
        """
        if email.to == self.refused_address:
            raise smtplib.SMTPRecipientsRefused({email.to: (550, b"No such user")})
        super().send(email)


@pytest.mark.usefixtures("bouncing_addresses")
async def test_each_address_is_emailed_once_with_the_given_email(email_sender: FakeEmailSender) -> None:
    """Tests every address is sent the given subject and body, and an address given twice is only emailed once."""
    await confirm_addresses_can_receive_email(
        ["one@example.com", "two@example.com", "one@example.com"], SUBJECT, HTML_BODY, email_sender
    )

    assert sorted(email.to for email in email_sender.sent) == ["one@example.com", "two@example.com"]
    assert {(email.subject, email.html_body) for email in email_sender.sent} == {(SUBJECT, HTML_BODY)}


async def test_a_new_address_that_bounces_is_refused_by_name(
    bouncing_addresses: set[str], email_sender: FakeEmailSender
) -> None:
    """Tests a new address whose email bounces is refused with an error naming it, so the user knows which to fix."""
    bouncing_addresses.add("bounces@example.com")

    with pytest.raises(UndeliverableEmailError) as refused:
        await confirm_addresses_can_receive_email(
            ["fine@example.com", "bounces@example.com"], SUBJECT, HTML_BODY, email_sender
        )

    assert refused.value.address == "bounces@example.com"


async def test_a_known_recipient_is_emailed_without_waiting_for_a_bounce(
    session: Session, bouncing_addresses: set[str], email_sender: FakeEmailSender
) -> None:
    """Tests an address that is already a recipient was confirmed before, so it is emailed without watching for a
    bounce (a late bounce is not waited for), while still being sent the email."""
    session.add(DBRecipient(email="known@example.com"))
    session.flush()
    bouncing_addresses.add("known@example.com")

    await confirm_addresses_can_receive_email(["known@example.com"], SUBJECT, HTML_BODY, email_sender)

    assert [email.to for email in email_sender.sent] == ["known@example.com"]


@pytest.mark.usefixtures("bouncing_addresses")
async def test_a_known_recipient_the_mail_server_refuses_is_refused(session: Session) -> None:
    """Tests an address that is already a recipient is still refused if the mail server refuses it outright."""
    session.add(DBRecipient(email="gone@example.com"))
    session.flush()

    with pytest.raises(UndeliverableEmailError) as refused:
        await confirm_addresses_can_receive_email(
            ["gone@example.com"], SUBJECT, HTML_BODY, RefusingEmailSender("gone@example.com")
        )

    assert refused.value.address == "gone@example.com"


@pytest.mark.usefixtures("bouncing_addresses")
async def test_emails_are_sent_without_stopping_the_dashboard_responding() -> None:
    """Tests the emails are sent from threads, so other work (e.g. dashboard requests) carries on while one sends."""
    other_work_ran = threading.Event()
    sent_while_other_work_ran: list[bool] = []

    class SlowEmailSender(FakeEmailSender):
        def send(self, email: OutgoingEmail) -> None:
            sent_while_other_work_ran.append(other_work_ran.wait(timeout=5))
            super().send(email)

    async def other_work() -> None:
        other_work_ran.set()

    await asyncio.gather(
        confirm_addresses_can_receive_email(["someone@example.com"], SUBJECT, HTML_BODY, SlowEmailSender()),
        other_work(),
    )

    assert sent_while_other_work_ran == [True]
