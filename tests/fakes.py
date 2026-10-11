from types import TracebackType
from typing import Self

from app.backend.email_service.message_builder import OutgoingEmail


class FakeEmailSender:
    """Records the emails the app would send, instead of sending them.

    Attributes:
        sent: Every email sent, in order.
        connections_opened: How many times it was used as a context manager, i.e. how many mail server
            connections a real sender would have opened for a batch of emails.
    """

    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []
        self.connections_opened: int = 0

    def __enter__(self) -> Self:
        self.connections_opened += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def send(self, email: OutgoingEmail) -> None:
        """Records an email as sent.

        Args:
            email: The email.
        """
        self.sent.append(email)
