from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import mktime_tz, parsedate_tz

from app.backend.email_service.message_builder import OutgoingEmail, build_message, html_to_text

EMAIL = OutgoingEmail(to="recipient@example.com", subject="Test Subject", html_body="<h2>Fees</h2><p>Fee is $50.</p>")


def test_build_message_sets_the_headers() -> None:
    """Tests the message is addressed, dated and marked as automated, with a Message-ID on the sender's domain."""
    msg: EmailMessage = build_message(EMAIL, "sender@example.com", sent_at=datetime(2026, 6, 1, 12, tzinfo=UTC))

    assert msg["Subject"] == "Test Subject"
    assert msg["From"] == "sender@example.com"
    assert msg["To"] == "recipient@example.com"
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg["Date"] == "Mon, 01 Jun 2026 12:00:00 -0000"
    assert msg["Message-ID"].endswith("@example.com>")


def test_build_message_dates_the_message_now_by_default() -> None:
    """Tests a message built without a send time is dated with the time it was built."""
    before: float = datetime.now(UTC).timestamp()

    msg: EmailMessage = build_message(EMAIL, "sender@example.com")

    date_parts = parsedate_tz(msg["Date"])
    assert date_parts is not None
    assert int(before) <= mktime_tz(date_parts) <= datetime.now(UTC).timestamp()


def test_build_message_has_a_plain_text_copy_before_the_html() -> None:
    """Tests email apps that can't show HTML get the same content as text, and HTML is preferred when they can."""
    msg: EmailMessage = build_message(EMAIL, "sender@example.com")

    parts = list(msg.iter_parts())
    assert [part.get_content_type() for part in parts] == ["text/plain", "text/html"]
    assert parts[0].get_content().strip() == "Fees\nFee is $50."
    assert "<p>Fee is $50.</p>" in parts[1].get_content()


def test_html_to_text_keeps_each_line_and_drops_blank_lines() -> None:
    html = "<div>\n  <h2>Title</h2>\n\n  <ul><li>First   item</li><li>Second</li></ul>\n</div>"

    assert html_to_text(html) == "Title\nFirst item\nSecond"


def test_each_message_gets_its_own_message_id() -> None:
    """Tests two emails never share a Message-ID, which email apps and spam filters treat as the same email."""
    first: EmailMessage = build_message(EMAIL, "sender@example.com")
    second: EmailMessage = build_message(EMAIL, "sender@example.com")

    assert first["Message-ID"] != second["Message-ID"]


def test_message_id_uses_the_senders_domain_when_the_sender_has_a_name() -> None:
    """Tests a sender written with a display name still gives a Message-ID on its own domain."""
    msg: EmailMessage = build_message(EMAIL, "Inwebstigator <sender@reports.example.org>")

    assert msg["Message-ID"].endswith("@reports.example.org>")


def test_html_to_text_keeps_inline_tags_on_their_line_and_puts_each_cell_on_its_own() -> None:
    """Tests highlighted words and links stay inside their sentence, while table cells (e.g. Before and After) each
    get a line."""
    html = (
        '<p>Fee is <span class="word-changed">$60</span> per <a href="https://example.com">year</a>.</p>'
        "<table><tr><td>- Fee is $50.</td><td>+ Fee is $60.</td></tr></table>"
    )

    assert html_to_text(html) == "Fee is $60 per year.\n- Fee is $50.\n+ Fee is $60."
