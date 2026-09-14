from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Config, config
from app.email_sender import build_message, notifier
from app.email_sender.notifier import Change, diff_rows

NOW = datetime(2026, 8, 5, 6, 0, tzinfo=UTC)
SITE = "example.edu.au"


def change(type_="PAGE_ADDED", url="https://example.edu.au/p", label=None):
    return Change(type=type_, url=url, label=label)


#  the input contract

def test_change_rejects_an_unknown_type():
    # A typo'd type would otherwise reach the email and be silently dropped,
    # because the digest only prints the types listed in SECTIONS.
    with pytest.raises(ValueError):
        Change(type="PAGE_EXPLODED", url="https://example.edu.au/p")


def test_change_is_frozen():
    # The notifier has no business editing what the diff finder handed it.
    with pytest.raises(FrozenInstanceError):
        change().url = "https://elsewhere.example"


#  rendering

def test_digest_subject_counts_the_changes():
    subject, _ = notifier.render_digest([change(), change()], NOW, SITE)
    assert "2 changes detected" in subject
    assert "05 Aug 2026" in subject


def test_digest_subject_is_singular_for_one_change():
    subject, _ = notifier.render_digest([change()], NOW, SITE)
    assert "1 change detected" in subject


def test_digest_prefers_the_label_but_always_shows_the_url():
    _, body = notifier.render_digest(
        [change(url="https://example.edu.au/fees", label="Fees")], NOW,
        SITE
    )
    assert "* Fees" in body
    assert "https://example.edu.au/fees" in body


def test_digest_falls_back_to_the_url_without_a_label():
    _, body = notifier.render_digest([change(url="https://example.edu.au/fees")], NOW, SITE)
    assert "* https://example.edu.au/fees" in body


def test_digest_orders_sections_and_skips_the_empty_ones():
    _, body = notifier.render_digest(
        [change("PAGE_ADDED"), change("PAGE_CONTENT_CHANGED")], NOW,
        SITE
    )
    assert body.index("Watched pages changed") < body.index("New pages")
    assert "Files changed" not in body
    assert "no longer reachable" not in body


def test_all_clear_names_the_window():
    _, body = notifier.render_all_clear(NOW, NOW - timedelta(days=7), SITE)
    assert "29 Jul 2026" in body
    assert "05 Aug 2026" in body


def test_build_message_addresses_the_client_and_flags_itself_as_a_bot():
    msg = build_message.build_message("subject line", "body text")
    assert msg["Subject"] == "subject line"
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg["To"] == ", ".join(build_message.CLIENT_TO)
    assert "body text" in msg.get_content()


#  deciding what goes out

@pytest.fixture
def sent(monkeypatch):
    """Capture what notify() would send instead of touching a mail server."""
    outbox = []

    def fake_send(msg, dry_run=False):
        outbox.append(msg)
        return True

    monkeypatch.setattr(notifier, "send_email", fake_send)
    return outbox


def test_notify_sends_a_digest_when_there_are_changes(sent):
    assert notifier.notify([change()], now=NOW, site_name=SITE) == "digest"
    assert len(sent) == 1


def test_notify_stays_quiet_when_nothing_changed_and_the_week_is_young(sent):
    action = notifier.notify([], now=NOW, last_email_at=NOW - timedelta(days=2), site_name=SITE)
    assert action == "nothing"
    assert sent == []


def test_notify_sends_the_all_clear_once_the_week_is_up(sent):
    action = notifier.notify([], now=NOW, last_email_at=NOW - timedelta(days=7), site_name=SITE)
    assert action == "all_clear"
    assert "No changes detected" in sent[0]["Subject"]


def test_all_clear_tolerates_a_run_that_drifts_early():
    # Cron never fires at the same second, so day 7 arrives a few minutes
    # short of 7*24h. That must still count, or it slips to day 8.
    drifted = NOW - timedelta(days=7) + timedelta(minutes=20)
    assert notifier.notify([], now=NOW, last_email_at=drifted, dry_run=True, site_name=SITE) == "all_clear"


def test_notify_never_sends_an_all_clear_without_a_last_email_time(sent):
    # No last_email_at means we can't tell a quiet week from a dead scraper,
    # so claiming "monitoring is running normally" would be a lie.
    assert notifier.notify([], now=NOW, last_email_at=None, site_name=SITE) == "nothing"
    assert sent == []


def test_changes_win_over_the_heartbeat(sent):
    action = notifier.notify([change()], now=NOW, last_email_at=NOW - timedelta(days=30), site_name=SITE)
    assert action == "digest"
    assert len(sent) == 1


def test_notify_reports_a_failed_send(monkeypatch):
    monkeypatch.setattr(notifier, "send_email", lambda msg, dry_run=False: False)
    assert notifier.notify([change()], now=NOW, site_name=SITE) == "failed"


def test_dry_run_does_not_open_an_smtp_connection(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("dry_run must not reach the network")

    monkeypatch.setattr(build_message.smtplib, "SMTP", explode)
    assert notifier.notify([change()], now=NOW, dry_run=True, site_name=SITE) == "digest"


#  settings

# Settings come from the shared Config in app/core/config.py, not a second
# .env loader in here. These check we read the right fields off it and the
# defaults stay blank.


def test_dry_run_reads_the_usual_spellings(monkeypatch):
    for raw in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("DRY_RUN", raw)
        assert Config().dry_run is True
    for raw in ("0", "false", "no", "off"):
        monkeypatch.setenv("DRY_RUN", raw)
        assert Config().dry_run is False


def test_dry_run_defaults_to_on_when_unset(monkeypatch):
    # A fresh checkout must not be able to mail anyone.
    monkeypatch.delenv("DRY_RUN", raising=False)

    assert Config().dry_run is True


def test_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.uwa.edu.au")

    assert Config().smtp_host == "smtp.uwa.edu.au"


def test_the_sender_reads_its_settings_off_the_shared_config():
    # One loader, not two. Whatever Config worked out is what we send with.
    assert config.client_to == build_message.CLIENT_TO
    assert config.smtp_host == build_message.SMTP_HOST
    assert config.email == build_message.SMTP_USER
    assert config.dry_run == build_message.DRY_RUN


def test_the_from_address_is_the_account_we_authenticate_as():
    # One setting, not two that can drift apart.
    assert build_message.FROM_ADDR == build_message.SMTP_USER


def test_the_defaults_are_blank_rather_than_plausible(monkeypatch):
    # A real looking default just mails nobody and looks fine doing it.
    # configuration_problems() turns a blank one into a proper failure.
    for name in ("CLIENT_TO", "SMTP_HOST", "EMAIL", "DRY_RUN"):
        monkeypatch.delenv(name, raising=False)
    defaults = Config()

    assert defaults.dry_run is True
    assert defaults.client_to == []
    assert defaults.smtp_host == ""
    assert defaults.email == ""


#  the DRY_RUN safety catch

def test_send_email_defaults_to_the_dry_run_setting(monkeypatch):
    # The point of the default: a fresh checkout with no .env must not be able
    # to mail whatever CLIENT_TO happens to fall back to.
    monkeypatch.setattr(build_message, "DRY_RUN", True)
    monkeypatch.setattr(build_message.smtplib, "SMTP", _explode)

    assert build_message.send_email(build_message.build_message("s", "b")) is True


def test_an_explicit_dry_run_false_overrides_the_setting(monkeypatch):
    monkeypatch.setattr(build_message, "DRY_RUN", True)
    monkeypatch.setattr(build_message.smtplib, "SMTP", _FakeSMTP)

    assert build_message.send_email(build_message.build_message("s", "b"), dry_run=False) is True
    assert _FakeSMTP.sent


def test_a_dead_mail_server_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(build_message, "DRY_RUN", False)
    monkeypatch.setattr(build_message.smtplib, "SMTP", _explode)

    assert build_message.send_email(build_message.build_message("s", "b")) is False


def _explode(*args, **kwargs):
    raise OSError("mail server is down")


class _FakeSMTP:
    sent = []

    def __init__(self, *args, **kwargs):
        type(self).sent = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        type(self).sent.append(msg)


#  side by side diffs for content changes

OLD_PAGE = "Applications close on 15 July.\nA late fee of $150 applies.\nContact the Student Centre."
NEW_PAGE = "Applications close on 1 August.\nA late fee of $150 applies.\nContact the Student Centre."


def changed_page(old=OLD_PAGE, new=NEW_PAGE):
    return Change(
        type="PAGE_CONTENT_CHANGED",
        url="https://uwa.edu.au/enrolment",
        label="Enrolment deadlines",
        old_text=old,
        new_text=new,
    )


def test_a_change_without_text_has_no_diff():
    # Added and removed pages have nothing to compare, and the diff finder
    # isn't passing text through yet either. Both must still email fine.
    assert change().has_diff is False
    assert changed_page().has_diff is True


def test_diff_rows_pairs_the_changed_line_side_by_side():
    rows = diff_rows(OLD_PAGE, NEW_PAGE)
    changed = [r for r in rows if r[0] == "changed"]

    assert len(changed) == 1
    _, left, right = changed[0]
    assert "15 July" in left
    assert "1 August" in right


def test_diff_rows_marks_only_the_words_that_differ():
    # Line level highlighting alone leaves the client hunting for the word.
    _, left, right = next(r for r in diff_rows(OLD_PAGE, NEW_PAGE) if r[0] == "changed")

    assert "<del" in left and "15 July" in left
    assert "<ins" in right and "1 August" in right
    # The unchanged part of the line must not be marked up.
    assert "<del" not in left.split("<del")[0]
    assert "Applications close on" in left


def test_diff_rows_reports_added_and_removed_lines():
    rows = diff_rows("one\ntwo", "one\ntwo\nthree")
    assert any(kind == "added" and "three" in right for kind, _, right in rows)

    rows = diff_rows("one\ntwo\nthree", "one\ntwo")
    assert any(kind == "removed" and "three" in left for kind, left, _ in rows)


def test_diff_rows_skips_long_unchanged_stretches():
    old = "\n".join(["same"] * 30 + ["before"])
    new = "\n".join(["same"] * 30 + ["after"])
    rows = diff_rows(old, new)

    assert any(kind == "skip" for kind, _, _ in rows)
    assert len(rows) < 30, "the whole unchanged block was included"


def test_diff_rows_are_capped_so_one_rewrite_cant_blow_up_the_email():
    old = "\n".join(f"line {i}" for i in range(500))
    new = "\n".join(f"changed {i}" for i in range(500))
    rows = diff_rows(old, new)

    assert len(rows) == notifier.DIFF_MAX_ROWS + 1
    assert "more rows" in rows[-1][1]


def test_blank_lines_do_not_count_as_changes():
    # Scraped text is full of blank lines that move whenever markup is touched.
    rows = diff_rows("one\n\n\ntwo", "one\ntwo")
    assert all(kind == "equal" for kind, _, _ in rows)


def test_a_whitespace_only_change_says_so_instead_of_showing_a_table():
    html = notifier.render_digest_html([changed_page(old="one\n\n\ntwo", new="one\ntwo")], NOW, SITE)

    assert "<table" not in html
    assert "no visible difference" in html


#  the html part

def test_html_digest_contains_a_before_after_table():
    html = notifier.render_digest_html([changed_page()], NOW, SITE)

    assert "<table" in html
    assert ">Before<" in html and ">After<" in html
    assert "1 August" in html


def test_html_digest_omits_the_table_when_there_is_no_text():
    html = notifier.render_digest_html([change()], NOW, SITE)
    assert "<table" not in html


def test_html_escapes_scraped_page_text():
    # Page text comes off a third party site. It must never become markup.
    html = notifier.render_digest_html(
        [changed_page(old="Fees are fine", new='Fees <script>alert("x")</script>')], NOW,
        SITE
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_html_escapes_the_label_and_url():
    html = notifier.render_digest_html(
        [Change(type="PAGE_ADDED", url="https://x.edu/?a=1&b=2", label="<b>bold</b>")], NOW,
        SITE
    )

    assert "<b>bold</b>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    assert "&amp;b=2" in html


def test_only_http_urls_become_clickable_links():
    for url in ("javascript:alert(1)", "data:text/html,<script>x</script>"):
        html = notifier.render_digest_html([Change(type="PAGE_ADDED", url=url)], NOW, SITE)
        assert "href=" not in html, f"{url} should not be a link"

    html = notifier.render_digest_html([Change(type="PAGE_ADDED", url="https://ok.edu/p")], NOW, SITE)
    assert 'href="https://ok.edu/p"' in html


#  the two parts together

def test_the_text_part_stacks_the_same_edits():
    # Side by side needs two columns; plain text has ~78 characters. The text
    # part must still show what changed, just stacked.
    _, body = notifier.render_digest([changed_page()], NOW, SITE)

    assert "- Applications close on 15 July." in body
    assert "+ Applications close on 1 August." in body
    assert "<del" not in body, "html markup leaked into the text part"


def test_a_digest_is_multipart_with_both_parts(sent):
    notifier.notify([changed_page()], now=NOW, site_name=SITE)
    msg = sent[0]

    assert msg.get_content_type() == "multipart/alternative"
    types = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in types and "text/html" in types


def test_the_all_clear_stays_plain_text(sent):
    # Nothing changed means nothing to diff, so there's no reason for HTML.
    notifier.notify([], now=NOW, last_email_at=NOW - timedelta(days=7), site_name=SITE)

    assert sent[0].get_content_type() == "text/plain"


#  headers that decide whether the client ever sees the email

def test_every_message_carries_a_date_and_message_id():
    # RFC 5322 requires Date, and spam filters read a missing Message-ID as a
    # bulk sender. Neither is added for us, so a report without them risks the
    # junk folder -- which looks exactly like the scraper being broken.
    msg = build_message.build_message("subject", "body", now=NOW)

    assert msg["Date"], "no Date header"
    assert msg["Message-ID"], "no Message-ID header"


def test_the_date_header_matches_the_run_time():
    msg = build_message.build_message("subject", "body", now=NOW)
    assert "18 Aug 2026" not in msg["Date"]
    assert "05 Aug 2026" in msg["Date"]


def test_the_message_id_uses_the_sending_domain(monkeypatch):
    # A Message-ID whose domain doesn't match the sender scores badly.
    monkeypatch.setattr(build_message, "FROM_ADDR", "Scan <scan@horizon.example.org>")
    msg = build_message.build_message("subject", "body", now=NOW)

    assert msg["Message-ID"].endswith("@horizon.example.org>")


def test_message_ids_are_unique_per_email():
    a = build_message.build_message("s", "b", now=NOW)["Message-ID"]
    b = build_message.build_message("s", "b", now=NOW)["Message-ID"]
    assert a != b


#  the client reads these in Perth, not UTC

def test_times_are_shown_in_the_report_timezone():
    # The 06:00 UTC run is 14:00 in Perth. A report headed 06:00 that the
    # client reads over lunch invites a support email.
    _, body = notifier.render_digest([change()], NOW, SITE)

    assert "14:00 AWST" in body
    assert "UTC" not in body


def test_the_html_part_uses_the_same_local_time():
    html = notifier.render_digest_html([change()], NOW, SITE)
    assert "14:00 AWST" in html


def test_the_all_clear_shows_local_times_too():
    _, body = notifier.render_all_clear(NOW, NOW - timedelta(days=7), SITE)
    assert "AWST" in body


def test_heartbeat_arithmetic_stays_in_utc():
    # Display converts to Perth; the maths must not, or a daylight saving jump
    # somewhere would shift the weekly window.
    assert timedelta(days=7) - timedelta(hours=12) == notifier.HEARTBEAT_DUE
