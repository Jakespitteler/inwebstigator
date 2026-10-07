from dataclasses import dataclass, field
from typing import Self

from pydantic import SecretStr

from app.core.config import config

IMPLICIT_TLS_PORT: int = 465
KNOWN_IMAP_HOSTS: dict[str, str] = {
    "smtp.office365.com": "outlook.office365.com",
    "smtp-mail.outlook.com": "outlook.office365.com",
}


def guess_imap_host(smtp_host: str) -> str | None:
    """Works out the inbox (IMAP) server that goes with an outgoing mail (SMTP) server.

    Microsoft's servers have different names, so they are looked up. Most other providers name them the same
    way (e.g. smtp.gmail.com and imap.gmail.com).

    Args:
        smtp_host: The outgoing mail server, e.g. "smtp.gmail.com".

    Returns:
        The inbox server, or None if it cannot be worked out and must be set in the settings.
    """
    if smtp_host in KNOWN_IMAP_HOSTS:
        return KNOWN_IMAP_HOSTS[smtp_host]
    if smtp_host.startswith("smtp."):
        return smtp_host.replace("smtp.", "imap.", 1)
    return None


@dataclass(frozen=True, slots=True)
class RetrySettings:
    """How sending an email is retried after a temporary failure, such as a dropped connection.

    Attributes:
        max_attempts: The most times the email is tried, including the first try.
        min_wait_seconds: The shortest wait between tries.
        max_wait_seconds: The longest wait between tries.
        multiplier: How quickly the wait grows after each failed try.
    """

    max_attempts: int = 3
    min_wait_seconds: float = 2
    max_wait_seconds: float = 15
    multiplier: float = 1

    @classmethod
    def from_config(cls) -> Self:
        """Reads the settings from the app's configuration, at the time it is called.

        Returns:
            The settings currently configured.
        """
        return cls(
            max_attempts=config.email_retry_max_attempts,
            min_wait_seconds=config.email_retry_min_wait_seconds,
            max_wait_seconds=config.email_retry_max_wait_seconds,
            multiplier=config.email_retry_multiplier,
        )


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    """How to reach the outgoing mail server and who the emails are from.

    Attributes:
        host: The outgoing mail server, e.g. "smtp.gmail.com".
        port: 465 for a connection that is encrypted from the start, or 587 (or 25) to upgrade with STARTTLS.
        timeout_seconds: How long to wait for the server before giving up on a try.
        username: The account to log in as, or an empty string for a server that needs no login.
        password: The account's password. It is only read when logging in.
        sender_address: The address the emails are sent from.
        retry: How a temporary failure is retried.
    """

    host: str
    port: int
    timeout_seconds: int
    username: str
    password: SecretStr
    sender_address: str
    retry: RetrySettings = field(default_factory=RetrySettings)

    @property
    def uses_implicit_tls(self) -> bool:
        """Whether the connection is encrypted from the start, rather than upgraded with STARTTLS."""
        return self.port == IMPLICIT_TLS_PORT

    @classmethod
    def from_config(cls) -> Self:
        """Reads the settings from the app's configuration, at the time it is called.

        Returns:
            The settings currently configured.
        """
        return cls(
            host=config.smtp_host,
            port=config.smtp_port,
            timeout_seconds=config.smtp_timeout_seconds,
            username=config.email,
            password=config.email_password,
            sender_address=config.email,
            retry=RetrySettings.from_config(),
        )


@dataclass(frozen=True, slots=True)
class InboxSettings:
    """How to reach the sending account's inbox, which is where bounced emails come back to.

    Attributes:
        host: The inbox (IMAP) server, or None if it is not set and cannot be worked out.
        port: The inbox server's port for an encrypted connection.
        timeout_seconds: How long to wait for the server before giving up.
        username: The account to log in as.
        password: The account's password. It is only read when logging in.
        bounce_wait_seconds: How long to watch the inbox for a bounce after emailing a new address.
        bounce_poll_seconds: How often to check the inbox while watching.
    """

    host: str | None
    port: int
    timeout_seconds: int
    username: str
    password: SecretStr
    bounce_wait_seconds: int
    bounce_poll_seconds: int

    @classmethod
    def from_config(cls) -> Self:
        """Reads the settings from the app's configuration, at the time it is called.

        Returns:
            The settings currently configured.
        """
        return cls(
            host=config.imap_host or guess_imap_host(config.smtp_host),
            port=config.imap_port,
            timeout_seconds=config.smtp_timeout_seconds,
            username=config.email,
            password=config.email_password,
            bounce_wait_seconds=config.email_bounce_wait_seconds,
            bounce_poll_seconds=config.email_bounce_poll_seconds,
        )
