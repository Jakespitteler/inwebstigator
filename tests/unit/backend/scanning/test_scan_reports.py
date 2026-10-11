import smtplib
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import HttpUrl
from pytest_mock import MockerFixture
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.backend.email_service.message_builder import OutgoingEmail
from app.backend.scanning.scan_reports import send_reports_awaiting_email
from app.db.schema import Base
from app.db.services.scan_run_service import ScanRunService
from app.db.services.website_service import WebsiteService
from app.db.session import configure_sqlite_connection
from app.models.scan_run_models import ChangeCreate, ChangeKind, ScanRunCreate, ScanRunRead, ScanStatus
from app.models.website_models import WebsiteCreate, WebsiteRead
from tests.fakes import FakeEmailSender


class FailingEmailSender(FakeEmailSender):
    """Records the emails the app would send, except to addresses set to fail, which raise their error instead.

    Attributes:
        failures: The error to raise for each address that fails.
    """

    def __init__(self, failures: dict[str, Exception]) -> None:
        super().__init__()
        self.failures: dict[str, Exception] = failures

    def send(self, email: OutgoingEmail) -> None:
        """Records an email as sent, or raises the error set for its address.

        Args:
            email: The email.
        """
        if email.to in self.failures:
            raise self.failures[email.to]
        super().send(email)


@pytest.fixture(autouse=True)
def use_test_database(session: Session, mocker: MockerFixture) -> None:
    """Reads and saves scan reports, and when recipients were emailed, in the test's database."""
    mocker.patch("app.backend.scanning.scan_reports.db_context", side_effect=lambda: nullcontext(session))
    mocker.patch("app.backend.scanning.notifications.db_context", side_effect=lambda: nullcontext(session))


def _website(session: Session, url: str, recipient_emails: list[str]) -> WebsiteRead:
    """Adds a website with the given recipients."""
    return WebsiteService(session).create(WebsiteCreate(url=HttpUrl(url), recipient_emails=recipient_emails))


def _record_scan(
    session: Session, website: WebsiteRead, new_page: str | None = None, status: ScanStatus = ScanStatus.SUCCESS
) -> ScanRunRead:
    """Adds a scan to the website's history that found a new page, or nothing if no page is given."""
    changes = [ChangeCreate(kind=ChangeKind.INTERNAL_LINK_ADDED, url=HttpUrl(new_page))] if new_page else []
    return ScanRunService(session).create(
        ScanRunCreate(website_id=website.id, scanned_at=datetime.now(UTC), status=status, changes=changes)
    )


def _awaiting_email(session: Session) -> list[ScanRunRead]:
    """Returns the scans whose reports have not been emailed yet."""
    return ScanRunService(session).get_awaiting_email()


def test_each_recipient_is_sent_one_email_with_the_reports_for_all_their_websites(session: Session):
    """Tests the reports waiting to be sent are grouped into one email per recipient, then recorded as sent so they
    are not sent again."""
    fees = _website(session, "https://fees.example.com", ["both@example.com"])
    dates = _website(session, "https://dates.example.com", ["both@example.com", "dates@example.com"])
    _record_scan(session, fees, "https://fees.example.com/new")
    _record_scan(session, dates, "https://dates.example.com/new")
    _record_scan(session, dates)  # Found nothing, so has no report
    email_sender = FakeEmailSender()

    send_reports_awaiting_email(email_sender)

    by_recipient = {email.to: email for email in email_sender.sent}
    assert sorted(by_recipient) == ["both@example.com", "dates@example.com"]
    assert by_recipient["both@example.com"].subject == "Website updates: fees.example.com and dates.example.com"
    assert "https://fees.example.com/new" in by_recipient["both@example.com"].html_body
    assert "https://dates.example.com/new" in by_recipient["both@example.com"].html_body
    assert "https://fees.example.com/new" not in by_recipient["dates@example.com"].html_body
    assert email_sender.connections_opened == 1
    assert _awaiting_email(session) == []

    send_reports_awaiting_email(email_sender)

    assert len(email_sender.sent) == 2  # Nothing is sent twice


def test_a_report_whose_email_failed_is_sent_at_the_next_run(session: Session):
    """Tests the changes found while email is down are kept, and emailed once it is back, rather than lost."""
    website = _website(session, "https://example.com", ["someone@example.com"])
    _record_scan(session, website, "https://example.com/found-while-email-was-down")

    send_reports_awaiting_email(FailingEmailSender({"someone@example.com": ConnectionError("No internet")}))

    assert len(_awaiting_email(session)) == 1

    _record_scan(session, website, "https://example.com/found-after")
    email_sender = FakeEmailSender()
    send_reports_awaiting_email(email_sender)

    [email] = email_sender.sent
    assert "https://example.com/found-while-email-was-down" in email.html_body
    assert "https://example.com/found-after" in email.html_body
    assert _awaiting_email(session) == []


def test_one_recipients_failed_email_does_not_stop_the_others(session: Session):
    """Tests one recipient's failed email does not stop the remaining recipients being emailed, and only the reports
    in the failed email are kept to send again."""
    failing = _website(session, "https://failing.example.com", ["failing@example.com"])
    working = _website(session, "https://working.example.com", ["working@example.com"])
    failing_scan = _record_scan(session, failing, "https://failing.example.com/new")
    _record_scan(session, working, "https://working.example.com/new")
    email_sender = FailingEmailSender({"failing@example.com": smtplib.SMTPServerDisconnected("Connection lost")})

    send_reports_awaiting_email(email_sender)

    assert [email.to for email in email_sender.sent] == ["working@example.com"]
    assert [scan_run.id for scan_run in _awaiting_email(session)] == [failing_scan.id]


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPRecipientsRefused({"refused@example.com": (550, b"No such user")}),
        smtplib.SMTPDataError(554, b"Rejected as spam"),
    ],
    ids=["address-refused", "email-rejected"],
)
def test_an_email_that_will_always_fail_is_not_tried_again(session: Session, error: smtplib.SMTPException):
    """Tests a report is not sent again and again to an address that is refused, or to a server that rejects it."""
    website = _website(session, "https://example.com", ["refused@example.com"])
    _record_scan(session, website, "https://example.com/new")

    send_reports_awaiting_email(FailingEmailSender({"refused@example.com": error}))

    assert _awaiting_email(session) == []


def test_a_wrong_password_keeps_the_reports_to_send_once_it_is_fixed(session: Session):
    """Tests a mail server login failure, which the user can fix, does not lose the reports."""
    website = _website(session, "https://example.com", ["someone@example.com"])
    _record_scan(session, website, "https://example.com/new")
    login_failed = smtplib.SMTPAuthenticationError(535, b"Username and password not accepted")

    send_reports_awaiting_email(FailingEmailSender({"someone@example.com": login_failed}))

    assert len(_awaiting_email(session)) == 1


def test_reports_for_a_website_without_recipients_count_as_sent(session: Session):
    """Tests a dashboard-only website (no recipients) has its reports cleared without sending any email."""
    website = _website(session, "https://dashboard-only.example.com", [])
    _record_scan(session, website, status=ScanStatus.CONNECTION_ERROR)
    email_sender = FakeEmailSender()

    send_reports_awaiting_email(email_sender)

    assert email_sender.sent == []
    assert _awaiting_email(session) == []


def test_a_failed_scan_is_reported_to_the_websites_recipients(session: Session):
    """Tests a scan that ran into a problem (and found no changes) is still emailed, so recipients are not left to
    think nothing changed, and its emailed time is saved."""
    website = _website(session, "https://example.com", ["someone@example.com"])
    failed_scan = _record_scan(session, website, status=ScanStatus.CONNECTION_ERROR)
    email_sender = FakeEmailSender()
    before = datetime.now(UTC)

    send_reports_awaiting_email(email_sender)

    [email] = email_sender.sent
    assert email.to == "someone@example.com"
    assert email.subject == "Website update: example.com"
    [notified_scan] = ScanRunService(session).get_latest_for_website(website.id, limit=1)
    assert notified_scan.id == failed_scan.id
    assert notified_scan.notified_at is not None and before <= notified_scan.notified_at <= datetime.now(UTC)


def test_a_report_is_kept_when_one_of_its_websites_recipients_could_not_be_emailed(session: Session):
    """Tests a report shared by two recipients is kept to send again when only one of them could not be emailed, so
    the one who missed it still gets it."""
    website = _website(session, "https://example.com", ["reached@example.com", "missed@example.com"])
    scan = _record_scan(session, website, "https://example.com/new")
    email_sender = FailingEmailSender({"missed@example.com": ConnectionError("Connection reset")})

    send_reports_awaiting_email(email_sender)

    assert [email.to for email in email_sender.sent] == ["reached@example.com"]
    assert [scan_run.id for scan_run in _awaiting_email(session)] == [scan.id]


def test_reports_are_still_emailed_when_recording_them_as_sent_fails(session: Session, mocker: MockerFixture):
    """Tests a database error after the emails went out is logged rather than raised, so the run carries on, and
    the reports stay waiting rather than being wrongly recorded as sent."""
    website = _website(session, "https://example.com", ["someone@example.com"])
    _record_scan(session, website, "https://example.com/new")
    mocker.patch.object(ScanRunService, "mark_emailed", side_effect=RuntimeError("database is locked"))
    email_sender = FakeEmailSender()

    send_reports_awaiting_email(email_sender)

    assert [email.to for email in email_sender.sent] == ["someone@example.com"]
    assert len(_awaiting_email(session)) == 1


class SlowEmailSender(FakeEmailSender):
    """Records the emails the app would send, taking a moment over each like a real mail server, so two runs that
    finish together overlap while they send."""

    def send(self, email: OutgoingEmail) -> None:
        """Waits a moment, then records the email as sent.

        Args:
            email: The email.
        """
        time.sleep(0.2)
        super().send(email)


def test_two_runs_that_finish_together_email_each_report_once(tmp_path: Path, mocker: MockerFixture):
    """Tests two runs that send the waiting reports at the same time (e.g. Run All Scans and a scheduled check), each
    in its own thread with its own database session as in the app, email each report only once."""
    engine = create_engine(f"sqlite:///{tmp_path / 'reports.db'}", connect_args={"check_same_thread": False})
    event.listen(engine, "connect", configure_sqlite_connection)
    Base.metadata.create_all(engine)
    new_session = sessionmaker(bind=engine)

    @contextmanager
    def own_session() -> Generator[Session]:
        with new_session() as session, session.begin():
            yield session

    for module in ("scan_reports", "notifications"):
        mocker.patch(f"app.backend.scanning.{module}.db_context", side_effect=own_session)
    with own_session() as session:
        _record_scan(
            session, _website(session, "https://example.com", ["someone@example.com"]), status=ScanStatus.TRAFFIC_ERROR
        )
    email_sender = SlowEmailSender()

    runs = [threading.Thread(target=send_reports_awaiting_email, args=(email_sender,)) for _ in range(2)]
    for run in runs:
        run.start()
    for run in runs:
        run.join()

    assert [email.to for email in email_sender.sent] == ["someone@example.com"]
    engine.dispose()
