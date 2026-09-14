from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.config import config
from app.db import repository
from app.db.schema import DBPendingNotification, DBWebsite
from app.db.services.notification_service import NotificationService
from app.email_sender import notifier

NOW = datetime(2026, 8, 27, 6, 0, tzinfo=UTC)


@pytest.fixture()
def service(session: Session) -> NotificationService:
    return NotificationService(session)


@pytest.fixture()
def sent(monkeypatch) -> list:
    """Capture messages instead of sending them, and report success."""
    captured = []
    monkeypatch.setattr(notifier, "send_email", lambda msg, dry_run=None: (captured.append(msg), True)[1])
    return captured


@pytest.fixture()
def dead_mail_server(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "send_email", lambda msg, dry_run=None: False)


def a_change(url: str = "https://example.edu.au/enrolment") -> notifier.Change:
    return notifier.Change(type="PAGE_CONTENT_CHANGED", url=url, label="Enrolment", old_text="a", new_text="b")


#  notification state
# The notifier is stateless by design, so this is the only thing that
# remembers when we last emailed. Without it the weekly all-clear never fires.


def test_a_website_starts_with_no_email_history(service: NotificationService, test_website: DBWebsite):
    assert service.last_email_at(test_website.id) is None


def test_state_is_created_once_and_reused(service: NotificationService, test_website: DBWebsite):
    first = service.get_state(test_website.id)
    second = service.get_state(test_website.id)

    assert first.id == second.id


def test_a_digest_moves_the_last_email_time(service: NotificationService, test_website: DBWebsite):
    service.record_run(test_website.id, NOW, "digest")

    assert service.last_email_at(test_website.id) == NOW


def test_a_quiet_run_records_the_run_but_not_an_email(service: NotificationService, test_website: DBWebsite):
    # This is the one that matters: if a quiet run reset the email clock, a
    # site that never changes would reset its own weekly window every morning
    # and the all-clear would never become due.
    service.record_run(test_website.id, NOW, "nothing")

    assert service.last_email_at(test_website.id) is None
    assert service.has_run_since(test_website.id, NOW - timedelta(hours=1))


def test_an_all_clear_also_moves_the_last_email_time(service: NotificationService, test_website: DBWebsite):
    service.record_run(test_website.id, NOW, "all_clear")

    assert service.last_email_at(test_website.id) == NOW


def test_has_run_since_is_false_before_the_first_run(service: NotificationService, test_website: DBWebsite):
    assert not service.has_run_since(test_website.id, NOW)


def test_has_run_since_is_false_for_a_later_cutoff(service: NotificationService, test_website: DBWebsite):
    service.record_run(test_website.id, NOW, "nothing")

    assert not service.has_run_since(test_website.id, NOW + timedelta(hours=1))


def test_stored_times_come_back_comparable(service: NotificationService, test_website: DBWebsite, sent):
    # SQLite has no timezone-aware column type, so a stored datetime reads back
    # naive. Handing that straight to the notifier raises on the
    # `now - last_email_at` comparison and takes the whole run down.
    service.record_run(test_website.id, NOW, "digest")
    service._db.expire_all()

    last = service.last_email_at(test_website.id)
    assert last is not None and last.tzinfo is not None
    assert NOW - last == timedelta(0)


def test_the_weekly_all_clear_fires_off_stored_state(
    service: NotificationService, test_website: DBWebsite, sent: list
):
    service.record_run(test_website.id, NOW, "digest")
    service._db.expire_all()

    action = service.run_for_website(test_website, [], now=NOW + timedelta(days=7))

    assert action == "all_clear"
    assert len(sent) == 1


#  failed sends
# A dropped send used to lose the day's changes outright: the diff finder has
# already moved its snapshot on, so tomorrow's run won't see them again.


def test_a_failed_send_parks_the_changes(
    service: NotificationService, test_website: DBWebsite, dead_mail_server
):
    action = service.run_for_website(test_website, [a_change()], now=NOW)

    assert action == "failed"
    parked = repository.get_list(service._db, table=DBPendingNotification)
    assert len(parked) == 1
    assert parked[0].changes[0]["url"] == "https://example.edu.au/enrolment"


def test_a_parked_change_survives_the_round_trip(
    service: NotificationService, test_website: DBWebsite, dead_mail_server
):
    service.run_for_website(test_website, [a_change()], now=NOW)
    parked = repository.get_list(service._db, table=DBPendingNotification)[0]

    restored = notifier.Change.from_dict(parked.changes[0])

    assert restored == a_change()


def test_from_dict_ignores_keys_it_does_not_know():
    # A parked change written by an older version must still be sendable after
    # a deploy, rather than stranding the queue.
    restored = notifier.Change.from_dict({"type": "PAGE_ADDED", "url": "https://x.test", "future_field": 1})

    assert restored.url == "https://x.test"


def test_a_failed_all_clear_is_not_parked(
    service: NotificationService, test_website: DBWebsite, dead_mail_server
):
    # It carries no changes, and next week's note says the same thing.
    service.record_run(test_website.id, NOW, "digest")
    service.run_for_website(test_website, [], now=NOW + timedelta(days=7))

    assert repository.get_list(service._db, table=DBPendingNotification) == []


def test_a_parked_report_is_not_retried_before_it_is_due(
    service: NotificationService, test_website: DBWebsite, dead_mail_server
):
    service.run_for_website(test_website, [a_change()], now=NOW)

    assert service.due_pending(NOW + timedelta(seconds=1)) == []


def test_a_parked_report_is_retried_once_it_is_due(
    service: NotificationService, test_website: DBWebsite, monkeypatch
):
    monkeypatch.setattr(notifier, "send_email", lambda msg, dry_run=None: False)
    service.run_for_website(test_website, [a_change()], now=NOW)

    later = NOW + timedelta(seconds=config.retry_base_delay_seconds + 1)
    monkeypatch.setattr(notifier, "send_email", lambda msg, dry_run=None: True)
    sent, queued = service.retry_pending(now=later)

    assert (sent, queued) == (1, 0)
    assert repository.get_list(service._db, table=DBPendingNotification) == []


def test_a_successful_retry_records_the_email(
    service: NotificationService, test_website: DBWebsite, monkeypatch
):
    monkeypatch.setattr(notifier, "send_email", lambda msg, dry_run=None: False)
    service.run_for_website(test_website, [a_change()], now=NOW)

    later = NOW + timedelta(seconds=config.retry_base_delay_seconds + 1)
    monkeypatch.setattr(notifier, "send_email", lambda msg, dry_run=None: True)
    service.retry_pending(now=later)

    assert service.last_email_at(test_website.id) == later


def test_a_failed_retry_backs_off_further(
    service: NotificationService, test_website: DBWebsite, dead_mail_server
):
    service.run_for_website(test_website, [a_change()], now=NOW)
    first = repository.get_list(service._db, table=DBPendingNotification)[0]
    first_gap = first.next_attempt_at.replace(tzinfo=UTC) - NOW

    later = NOW + timedelta(seconds=config.retry_base_delay_seconds + 1)
    sent, queued = service.retry_pending(now=later)
    second = repository.get_list(service._db, table=DBPendingNotification)[0]
    second_gap = second.next_attempt_at.replace(tzinfo=UTC) - later

    assert (sent, queued) == (0, 1)
    assert second.attempts == 2
    assert second_gap > first_gap, "backoff did not grow after a second failure"


def test_the_backoff_is_capped(service: NotificationService):
    huge = service._backoff(50)

    assert huge == timedelta(seconds=config.retry_max_delay_seconds)


def test_a_permanently_broken_send_is_eventually_given_up_on(
    service: NotificationService, test_website: DBWebsite, dead_mail_server, caplog
):
    # A queue that never drains is worse than a loud failure.
    service.run_for_website(test_website, [a_change()], now=NOW)

    at = NOW
    for _ in range(config.retry_max_attempts + 2):
        at += timedelta(seconds=config.retry_max_delay_seconds)
        service.retry_pending(now=at)

    assert repository.get_list(service._db, table=DBPendingNotification) == []
    assert "giving up" in caplog.text


#  multi-site
# The database models many websites, so a report names the site it is about
# rather than every report carrying one global name.


def test_each_report_is_stamped_with_its_own_site(
    service: NotificationService, session: Session, test_website: DBWebsite, sent: list
):
    other = DBWebsite(url="https://other.edu.au")
    repository.add(session, record=other)

    service.run_for_website(test_website, [a_change()], now=NOW)
    service.run_for_website(other, [a_change()], now=NOW)

    subjects = [msg["Subject"] for msg in sent]
    assert test_website.url in subjects[0]
    assert other.url in subjects[1]


def test_one_site_going_quiet_does_not_affect_another(
    service: NotificationService, session: Session, test_website: DBWebsite, sent: list
):
    other = DBWebsite(url="https://other.edu.au")
    repository.add(session, record=other)

    service.record_run(test_website.id, NOW, "digest")

    assert service.last_email_at(test_website.id) == NOW
    assert service.last_email_at(other.id) is None
