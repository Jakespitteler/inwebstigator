import uuid
from typing import Any


class DomainError(Exception):
    """Base class for all domain exceptions."""

    ...


class NotFoundError(DomainError):
    """
    Exception raised when a record is not found.

    Attributes:
        id: The UUID of the record that was not found.
    """

    def __init__(
        self,
        id: uuid.UUID | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if id:
            self.id: uuid.UUID = id
            super().__init__(f"{id=} not found.")
        elif attributes:
            self.attributes: dict[str, Any] = attributes
            super().__init__(f"{attributes=} not found.")
        else:
            super().__init__("not found.")


class IntegrityError(DomainError):
    """
    Exception raised when data integrity constraints are violated.
    """

    def __init__(self) -> None:
        super().__init__("Data validation error. Ensure all referenced IDs exist and unique constraints are met.")
