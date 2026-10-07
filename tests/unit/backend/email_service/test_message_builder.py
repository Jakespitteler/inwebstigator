from datetime import UTC, datetime
from email.message import EmailMessage

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
    msg: EmailMessage = build_message(EMAIL, "sender@example.com")

    assert msg["Date"] is not None


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
