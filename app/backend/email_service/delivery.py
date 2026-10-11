import imaplib
import logging
import smtplib
import ssl
import threading
import time
from contextlib import suppress
from email.message import EmailMessage
from types import TracebackType
from typing import Protocol, Self

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.backend.email_service.message_builder import OutgoingEmail, build_message
from app.backend.email_service.settings import InboxSettings, RetrySettings, SmtpSettings
from app.core.errors import UndeliverableEmailError

PERMANENT_SMTP_ERRORS: tuple[type[smtplib.SMTPException], ...] = (
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPSenderRefused,
)
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (smtplib.SMTPException, TimeoutError, ConnectionError)
FIRST_PERMANENT_REPLY_CODE: int = 500
BOUNCE_SENDERS_SEARCH: str = '(OR FROM "mailer-daemon" FROM "postmaster")'
# What comes just before an address in delivery failure emails, e.g. "delivered to bob@x.com", "<bob@x.com>" or
# "rfc822;bob@x.com". An inbox search matches any part of the text, so searching for the address alone would also
# match a longer address that ends with it (e.g. jimbob@x.com for bob@x.com). The last is a double quote, escaped as
# the search puts each one inside double quotes.
ADDRESS_PREFIXES: tuple[str, ...] = (" ", "<", ";", ":", "(", "'", '\\"')
logger: logging.Logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    """Anything that can send the app's emails, so a fake can stand in for the mail server in tests.

    Used as a context manager, a sender may keep one connection open for every email sent inside the block.
    """

    def send(self, email: OutgoingEmail) -> None:
        """Sends one email.

        Args:
            email: The recipient, subject and HTML body.
        """
        ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


def is_temporary_failure(error: BaseException) -> bool:
    """Checks whether a failed send might work if it is tried again.

    A dropped connection or a busy server (a 4xx reply) is temporary. A wrong password, a refused address or any
    other 5xx reply (e.g. rejected as spam, or too large) fails the same way every time.

    Args:
        error: Why the send failed.

    Returns:
        True if the send should be tried again.
    """
    if isinstance(error, PERMANENT_SMTP_ERRORS):
        return False
    if isinstance(error, smtplib.SMTPResponseException):
        return error.smtp_code < FIRST_PERMANENT_REPLY_CODE
    return isinstance(error, RETRYABLE_ERRORS)


def is_undeliverable(error: BaseException) -> bool:
    """Checks whether an email failed in a way that will happen every time it is sent, however long it is left.

    Only an address refused for good (a 5xx reply), or an email the server rejects (any other 5xx reply, e.g. marked
    as spam or too large), counts. An address refused only for now (a 4xx reply, e.g. a server that turns away a new
    sender at first) can work later, as can a wrong password or sender address (a setting the user can fix), no
    internet or a mail server that is down, so those can still be sent later.

    Args:
        error: Why the send failed, after any retries.

    Returns:
        True if the email should not be tried again later.
    """
    if isinstance(error, smtplib.SMTPAuthenticationError | smtplib.SMTPSenderRefused):
        return False
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return _is_refused_for_good(error)
    return isinstance(error, smtplib.SMTPResponseException) and error.smtp_code >= FIRST_PERMANENT_REPLY_CODE


def _is_refused_for_good(error: smtplib.SMTPRecipientsRefused) -> bool:
    """Checks whether the mail server refused an address for good (a 5xx reply, e.g. "No such user"), rather than only
    for now (a 4xx reply, e.g. "Try again later").

    Args:
        error: The refusal, with the server's reply for each refused address.

    Returns:
        True if any address was refused for good.
    """
    return any(code >= FIRST_PERMANENT_REPLY_CODE for code, _ in error.recipients.values())


def _retrying(settings: RetrySettings) -> Retrying:
    """Builds the retry loop for one email, from the settings at the time it is sent.

    Args:
        settings: How many tries to make and how long to wait between them.

    Returns:
        A tenacity retry loop that re-raises the last error once the tries run out.
    """
    return Retrying(
        wait=wait_exponential(
            multiplier=settings.multiplier,
            min=settings.min_wait_seconds,
            max=settings.max_wait_seconds,
        ),
        stop=stop_after_attempt(settings.max_attempts),
        retry=retry_if_exception(is_temporary_failure),
        reraise=True,
    )


def _new_connection(settings: SmtpSettings, context: ssl.SSLContext) -> smtplib.SMTP:
    """Opens a connection to the mail server, encrypted from the start when the port expects it.

    Args:
        settings: The mail server's address and port.
        context: The TLS settings, which check the server's certificate.

    Returns:
        The open connection, not yet logged in.
    """
    if settings.uses_implicit_tls:
        return smtplib.SMTP_SSL(
            host=settings.host,
            port=settings.port,
            timeout=settings.timeout_seconds,
            context=context,
        )
    return smtplib.SMTP(host=settings.host, port=settings.port, timeout=settings.timeout_seconds)


def connect_to_mail_server(settings: SmtpSettings) -> smtplib.SMTP:
    """Opens an encrypted, logged in connection to the outgoing mail server.

    The server's certificate is always checked, so the password cannot be sent to a server pretending to be the
    real one. Port 465 is encrypted from the start; any other port (e.g. 587) is upgraded with STARTTLS.

    Args:
        settings: The mail server's address, port and login.

    Returns:
        The connection, ready to send.

    Raises:
        smtplib.SMTPException: If the server refuses the encryption or the login.
        OSError: If the server cannot be reached.
    """
    context: ssl.SSLContext = ssl.create_default_context()
    connection: smtplib.SMTP = _new_connection(settings, context)
    try:
        if not settings.uses_implicit_tls:
            connection.starttls(context=context)
        if settings.username:
            connection.login(user=settings.username, password=settings.password.get_secret_value())
    except BaseException:
        connection.close()
        raise
    return connection


class SmtpEmailSender:
    """Sends emails through the configured SMTP server.

    Outside a `with` block, each email opens and closes its own connection. Inside one, the first email opens a
    connection that the rest reuse, so a run that emails many recipients logs in once. Emails sent from several
    threads at once take turns, so two are never written to the same connection at the same time.
    """

    def __init__(self, settings: SmtpSettings) -> None:
        self._settings: SmtpSettings = settings
        self._connection: smtplib.SMTP | None = None
        self._keep_connection_open: bool = False
        self._connection_lock: threading.Lock = threading.Lock()

    def __enter__(self) -> Self:
        self._keep_connection_open = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._keep_connection_open = False
        self._close_connection()

    def send(self, email: OutgoingEmail) -> None:
        """Sends one email, retrying temporary failures with exponential backoff.

        Args:
            email: The recipient, subject and HTML body.

        Raises:
            smtplib.SMTPException: If the server refuses the email, or keeps failing until the tries run out.
            TimeoutError: If the server keeps timing out until the tries run out.
            ConnectionError: If the connection keeps failing until the tries run out.
        """
        message: EmailMessage = build_message(email, sender_address=self._settings.sender_address)
        for attempt in _retrying(self._settings.retry):
            with attempt:
                self._send_once(message)

    def _send_once(self, message: EmailMessage) -> None:
        """Makes one try at sending a message, dropping the connection if it fails so the next try reconnects.

        Only one thread uses the connection at a time, as an SMTP connection cannot send two emails at once.

        Args:
            message: The built message.
        """
        with self._connection_lock:
            try:
                self._open_connection().send_message(message)
            except BaseException:
                self._close_connection()
                raise
            if not self._keep_connection_open:
                self._close_connection()

    def _open_connection(self) -> smtplib.SMTP:
        """Gets the open connection, connecting first if there isn't one.

        Returns:
            The connection to send with.
        """
        if self._connection is None:
            self._connection = connect_to_mail_server(self._settings)
        return self._connection

    def _close_connection(self) -> None:
        """Says goodbye to the server and closes the connection, if one is open, even if the server has gone."""
        if self._connection is None:
            return
        with suppress(smtplib.SMTPException, OSError):
            self._connection.quit()
        self._connection.close()
        self._connection = None


def get_email_sender() -> EmailSender:
    """Builds the app's email sender from the current settings.

    Used as a FastAPI dependency and by the scheduled scans, so tests can swap in a fake sender.

    Returns:
        A sender for the configured SMTP server.
    """
    return SmtpEmailSender(SmtpSettings.from_config())


def send_confirmation(email: OutgoingEmail, email_sender: EmailSender) -> None:
    """Sends an email to one address, e.g. to confirm it can receive email.

    Args:
        email: The address, subject and HTML body.
        email_sender: Sends the email.

    Raises:
        UndeliverableEmailError: If the mail server refuses the address straight away.
    """
    try:
        email_sender.send(email)
    except smtplib.SMTPRecipientsRefused as error:
        raise UndeliverableEmailError(email.to) from error


def _open_inbox(settings: InboxSettings) -> imaplib.IMAP4_SSL | None:
    """Logs in to the inbox of the account that sends the emails, which is where bounced emails are returned.

    The server's certificate is checked, so the password cannot be sent to a server pretending to be the real one.

    Args:
        settings: The inbox server and login.

    Returns:
        The logged in connection, or None if the inbox could not be reached.
    """
    if settings.host is None:
        logger.warning("No inbox (IMAP) server is set and it cannot be worked out, so bounces cannot be detected.")
        return None
    try:
        inbox = imaplib.IMAP4_SSL(
            host=settings.host,
            port=settings.port,
            ssl_context=ssl.create_default_context(),
            timeout=settings.timeout_seconds,
        )
        inbox.login(user=settings.username, password=settings.password.get_secret_value())
    except (imaplib.IMAP4.error, OSError):
        logger.warning("Could not open the inbox at %s, so bounced emails cannot be detected.", settings.host)
        return None
    return inbox


def _address_search(address: str) -> str:
    """Builds the inbox search for an email address, only where it starts after a space or punctuation.

    Args:
        address: The email address to look for. It must already be validated, as it is placed in the search.

    Returns:
        The search, e.g. `OR TEXT " bob@x.com" OR TEXT "<bob@x.com" ...`.
    """
    searches: list[str] = [f'TEXT "{prefix}{address}"' for prefix in ADDRESS_PREFIXES]
    combined: str = searches[-1]
    for search in reversed(searches[:-1]):
        combined = f"OR {search} {combined}"
    return combined


def _count_bounces(inbox: imaplib.IMAP4_SSL, address: str) -> int:
    """Counts the delivery failure emails in the inbox that mention an address.

    Gmail and most other providers send them from "mailer-daemon", and Microsoft 365 and Outlook from "postmaster".
    A delivery failure about a longer address that ends with this one (e.g. jimbob@x.com for bob@x.com) is not
    counted (see `_address_search`).

    Args:
        inbox: A logged in connection to the inbox.
        address: The email address to look for. It must already be validated, as it is placed in the search.

    Returns:
        The number of delivery failure emails that mention the address.
    """
    inbox.select(mailbox="INBOX", readonly=True)
    _, matches = inbox.search(None, f"({BOUNCE_SENDERS_SEARCH} ({_address_search(address)}))")
    return len((matches[0] or b"").split())


def _wait_for_bounce(inbox: imaplib.IMAP4_SSL, address: str, bounces_before: int, settings: InboxSettings) -> bool:
    """Waits for a delivery failure email about an address to arrive in the inbox.

    Args:
        inbox: A logged in connection to the inbox.
        address: The email address that was emailed.
        bounces_before: How many delivery failure emails already mentioned the address before it was emailed.
        settings: How long to wait, and how often to check.

    Returns:
        True if a new delivery failure email arrived before the wait ran out, otherwise False.
    """
    for _ in range(settings.bounce_wait_seconds // settings.bounce_poll_seconds):
        time.sleep(settings.bounce_poll_seconds)
        if _count_bounces(inbox, address) > bounces_before:
            return True
    return False


def confirm_address_can_receive_email(
    email: OutgoingEmail,
    email_sender: EmailSender,
    settings: InboxSettings | None = None,
) -> None:
    """Emails an address, then waits to see whether the email bounces.

    A mail server such as Gmail accepts an email first and only reports a missing mailbox afterwards, by sending
    a delivery failure email back to the sending account's inbox. So this watches that inbox for a short time.
    A bounce that arrives after the wait is not seen. If the inbox cannot be reached, the email is sent
    but its bounce cannot be watched for.

    Args:
        email: The confirmation email, addressed to the address being confirmed.
        email_sender: Sends the email.
        settings: The inbox to watch, and for how long. Defaults to the app's configured settings.

    Raises:
        UndeliverableEmailError: If the email is refused or bounces.
    """
    inbox_settings: InboxSettings = settings or InboxSettings.from_config()
    inbox: imaplib.IMAP4_SSL | None = _open_inbox(inbox_settings)
    if inbox is None:
        send_confirmation(email, email_sender)
        return

    with inbox:
        bounces_before: int = _count_bounces(inbox, email.to)
        send_confirmation(email, email_sender)
        if _wait_for_bounce(inbox, email.to, bounces_before, inbox_settings):
            raise UndeliverableEmailError(email.to)
