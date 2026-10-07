from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from bs4 import BeautifulSoup

LINE_BREAKING_TAGS: frozenset[str] = frozenset(
    {"br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "ol", "p", "table", "td", "th", "tr", "ul"}
)


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    """An email the app wants to send, before it is turned into a MIME message.

    Attributes:
        to: The recipient's email address.
        subject: The subject line.
        html_body: The email's content as HTML. A plain-text copy is made from it when the email is built.
    """

    to: str
    subject: str
    html_body: str


def _sender_domain(sender_address: str) -> str | None:
    """Extracts the domain portion of the sender's email address.

    It is used to construct compliant Message-ID headers.

    Args:
        sender_address: The address the email is sent from.

    Returns:
        The domain string if successfully parsed, otherwise None.
    """
    _, address = parseaddr(sender_address)
    _, _, domain = address.partition("@")
    return domain or None


def _out_of_office_blocking_headers() -> dict[str, str]:
    """Builds the headers that mark the email as sent by a bot, so no out-of-office replies come back.

    Returns:
        The header names and their values.
    """
    return {"Auto-Submitted": "auto-generated"}


def _spam_filter_headers(sent_at: datetime, sender_address: str) -> dict[str, str]:
    """Builds the headers Python doesn't add itself, without which spam filters read the email as bulk mail.

    For the same reason, the Message-ID domain matches the sender's domain.

    Args:
        sent_at: When the email is sent.
        sender_address: The address the email is sent from.

    Returns:
        The header names and their values.
    """
    return {"Date": formatdate(sent_at.timestamp()), "Message-ID": make_msgid(domain=_sender_domain(sender_address))}


def html_to_text(html: str) -> str:
    """Makes a plain-text copy of an email's HTML, for email apps that don't show HTML.

    Spam filters also score HTML-only emails as more likely to be spam, so every email has both.

    Args:
        html: The email's HTML.

    Returns:
        The visible text, with a line for each block of the HTML (e.g. each paragraph, list item or table cell)
        and no blank lines. Inline tags such as highlights and links stay on their line.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(list(LINE_BREAKING_TAGS)):
        tag.insert(0, "\n")
        tag.append("\n")
    lines: list[str] = [" ".join(line.split()) for line in soup.get_text().splitlines()]
    return "\n".join(line for line in lines if line)


def build_message(email: OutgoingEmail, sender_address: str, sent_at: datetime | None = None) -> EmailMessage:
    """Constructs a structured MIME EmailMessage instance without sending it.

    Populates standard metadata headers (Subject, From, To, Auto-Submitted, Date, Message-ID), then adds a
    plain-text copy of the body followed by the HTML body, so email apps show the HTML when they can.

    Args:
        email: The recipient, subject and HTML body.
        sender_address: The address the email is sent from.
        sent_at: When the email is sent. Defaults to the current UTC time if omitted.

    Returns:
        A fully constructed EmailMessage instance ready for transmission.
    """
    headers: dict[str, str] = {
        "Subject": email.subject,
        "From": sender_address,
        "To": email.to,
        **_out_of_office_blocking_headers(),
        **_spam_filter_headers(sent_at or datetime.now(UTC), sender_address),
    }

    message = EmailMessage()
    for name, value in headers.items():
        message[name] = value
    message.set_content(html_to_text(email.html_body))
    message.add_alternative(email.html_body, subtype="html")
    return message
