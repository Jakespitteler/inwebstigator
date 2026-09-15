import uuid
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy.orm import Session


class CRUDService[READ: BaseModel, CREATE: BaseModel, UPDATE: BaseModel](Protocol):
    """Protocol defining the standard interface for CRUD database services.

    Defines abstract signatures for generic Read, Create, and Update Pydantic data
    models operating over an underlying SQLAlchemy Session.

    Type Parameters:
        READ: Pydantic BaseModel type returned by reading operations.
        CREATE: Pydantic BaseModel type accepted for resource creation.
        UPDATE: Pydantic BaseModel type accepted for resource mutation.
    """

    def __init__(self, session: Session) -> None: ...
    def get_all(self, skip: int, limit: int) -> Sequence[READ]: ...
    def get(self, id: uuid.UUID) -> READ: ...
    def create(self, model_create: CREATE) -> READ: ...
    def update(self, id: uuid.UUID, model_update: UPDATE) -> READ: ...
    def delete(self, id: uuid.UUID) -> None: ...
