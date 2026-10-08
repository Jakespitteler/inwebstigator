import pytest
from pytest_mock import MockerFixture

from app.backend.email_service.message_builder import OutgoingEmail
from app.db.services.recipient_service import RecipientService
from app.models.recipient_models import RecipientRead
from app.scanning.notifications import (
    group_by_recipient,
    send_notification,
    send_notifications,
    send_notifications_skipping_failures,
)
from tests.fakes import FakeEmailSender


class FailingSender(FakeEmailSender):
    """A sender whose mail server cannot be reached."""

    def send(self, email: OutgoingEmail) -> None:
        raise ConnectionError("SMTP connection timed out")


def _email(to: str) -> OutgoingEmail:
    """Builds a report email to an address."""
    return OutgoingEmail(to=to, subject="Website update", html_body="<p>Report</p>")


def test_send_notification_success(
    mocker: MockerFixture, test_recipient: RecipientRead, email_sender: FakeEmailSender
) -> None:
    """Tests that send_notification sends the email, then updates last_email_at."""
    mocker.patch.object(RecipientService, "get_by_email", return_value=test_recipient)
    mock_recipient_service_update = mocker.patch.object(RecipientService, "update")

    send_notification(_email(test_recipient.email), email_sender)

    assert email_sender.sent == [_email(test_recipient.email)]
    mock_recipient_service_update.assert_called_once()
    assert mock_recipient_service_update.call_args.kwargs["id"] == test_recipient.id


def test_send_notification_failure(mocker: MockerFixture, test_recipient: RecipientRead) -> None:
    """Tests that send_notification propagates exceptions if email delivery fails, preventing metadata updates."""
    mock_recipient_service_update = mocker.patch.object(RecipientService, "update")

    with pytest.raises(ConnectionError, match="SMTP connection timed out"):
        send_notification(_email(test_recipient.email), FailingSender())

    mock_recipient_service_update.assert_not_called()


def test_send_notification_does_not_report_a_sent_email_as_failed(
    mocker: MockerFixture, test_recipient: RecipientRead, email_sender: FakeEmailSender
) -> None:
    """Tests that an email that went out is not reported as failed when recording it fails afterwards."""
    mocker.patch.object(RecipientService, "get_by_email", side_effect=RuntimeError("Database is locked"))

    send_notification(_email(test_recipient.email), email_sender)

    assert len(email_sender.sent) == 1


def test_send_notifications_uses_one_connection(email_sender: FakeEmailSender, mocker: MockerFixture) -> None:
    """Tests a report emailed to several recipients is sent over one connection to the mail server."""
    mocker.patch("app.scanning.notifications._record_email_sent")

    send_notifications([_email("a@gmail.com"), _email("b@gmail.com")], email_sender)

    assert [email.to for email in email_sender.sent] == ["a@gmail.com", "b@gmail.com"]
    assert email_sender.connections_opened == 1


def test_send_notifications_stops_at_the_first_failure(mocker: MockerFixture) -> None:
    """Tests a failed email is raised, so the caller knows the report did not reach everyone."""
    mock_send_notification = mocker.patch(
        "app.scanning.notifications.send_notification", side_effect=[ConnectionError("smtp down"), None]
    )

    with pytest.raises(ConnectionError, match="smtp down"):
        send_notifications([_email("a@gmail.com"), _email("b@gmail.com")], FakeEmailSender())

    assert mock_send_notification.call_count == 1


def test_send_notifications_skipping_failures_sends_the_rest(mocker: MockerFixture) -> None:
    """Tests one recipient's failed email does not stop the remaining recipients being emailed."""
    email_sender = FakeEmailSender()
    mock_send_notification = mocker.patch(
        "app.scanning.notifications.send_notification", side_effect=[ConnectionError("smtp down"), None]
    )

    send_notifications_skipping_failures([_email("a@gmail.com"), _email("b@gmail.com")], email_sender)

    assert [call.args[0].to for call in mock_send_notification.call_args_list] == ["a@gmail.com", "b@gmail.com"]
    assert email_sender.connections_opened == 1


def test_group_by_recipient_lists_each_item_under_each_of_its_recipients(test_recipient: RecipientRead) -> None:
    """Tests an item for two recipients is listed under both, keeping the order the items were given in."""
    other_recipient = test_recipient.model_copy(update={"email": "other@gmail.com"})
    recipients_by_item: dict[str, list[RecipientRead]] = {
        "first": [test_recipient, other_recipient],
        "second": [other_recipient],
        "unwatched": [],
    }

    grouped = group_by_recipient(recipients_by_item, lambda item: recipients_by_item[item])

    assert grouped == {test_recipient.email: ["first"], "other@gmail.com": ["first", "second"]}
