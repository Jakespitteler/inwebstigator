import smtplib
import ssl
from contextlib import suppress
from email.message import EmailMessage
from types import TracebackType
from typing import Protocol, Self

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.backend.email_service.message_builder import OutgoingEmail, build_message
from app.backend.email_service.settings import RetrySettings, SmtpSettings
from app.core.errors import UndeliverableEmailError

PERMANENT_SMTP_ERRORS: tuple[type[smtplib.SMTPException], ...] = (
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPSenderRefused,
)
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (smtplib.SMTPException, TimeoutError, ConnectionError)
FIRST_PERMANENT_REPLY_CODE: int = 500


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
    connection that the rest reuse, so a run that emails many recipients logs in once.
    """

    def __init__(self, settings: SmtpSettings) -> None:
        self._settings: SmtpSettings = settings
        self._connection: smtplib.SMTP | None = None
        self._keep_connection_open: bool = False

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

        Args:
            message: The built message.
        """
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
