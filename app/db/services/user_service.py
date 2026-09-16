import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db import repository
from app.db.schema import DBUser
from app.db.utils.interfaces import CRUDService
from app.models.user_models import UserCreate, UserRead, UserUpdate

logger: logging.Logger = logging.getLogger(__name__)


class UserService(CRUDService[UserRead, UserCreate, UserUpdate]):
    def __init__(self, session: Session):
        """Initialises the UserService with an active database session.

        Args:
            session: The SQLAlchemy database session object used for executing operations.
        """
        self._db = session

    def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[UserRead]:
        """Retrieves a paginated list of user records from the database.

        Args:
            skip: The number of initial records to skip for pagination. Defaults to 0.
            limit: The maximum number of records to return. Defaults to 100.

        Returns:
            A sequence of UserRead models representing the retrieved records.
        """
        user_records: Sequence[DBUser] = repository.get_list(self._db, table=DBUser, skip=skip, limit=limit)
        return [UserRead.model_validate(user_record) for user_record in user_records]

    def get(self, id: uuid.UUID) -> UserRead:
        """Retrieves a single user record by its unique primary key identifier.

        Args:
            id: The UUID identifier of the target user record.

        Returns:
            The matching UserRead data model instance.

        Raises:
            NotFoundError: If no user record matches the provided UUID.
        """
        user_record: DBUser = repository.get(
            self._db,
            table=DBUser,
            id=id,
        )
        return UserRead.model_validate(user_record)

    def get_by_url(self, url: str) -> UserRead:
        """Retrieves a single user record and its relationships by its associated URL attribute.

        Args:
            url: The URL string of the user record to retrieve.

        Returns:
            The matching UserRead data model instance.

        Raises:
            NotFoundError: If no user record exists with the specified URL.
        """
        user_records: Sequence[DBUser] = repository.get_list(
            self._db,
            table=DBUser,
            attributes={"url": url},
            limit=1,
        )

        if not user_records:
            raise NotFoundError(attributes={"url": url})

        return UserRead.model_validate(user_records[0])

    def create(self, model_create: UserCreate) -> UserRead:
        """Creates and persists a new user record in the database.

        Args:
            model_create: The UserCreate payload containing initial attributes.

        Returns:
            The created UserRead data model instance reflecting the saved state.

        Raises:
            IntegrityError: If the record violates database constraints or already exists.
        """
        user_record: DBUser = DBUser(**model_create.model_dump())
        repository.add(self._db, record=user_record)

        return UserRead.model_validate(user_record)

    def update(self, id: uuid.UUID, model_update: UserUpdate) -> UserRead:
        """Updates attributes of an existing user record by its primary key.

        Args:
            id: The UUID identifier of the user record to update.
            model_update: The UserUpdate schema containing fields to update.

        Returns:
            The updated UserRead data model instance.

        Raises:
            NotFoundError: If no user record matches the provided UUID.
            IntegrityError: If updated attribute values violate database constraints.
        """

        user_record: DBUser = repository.update(
            self._db,
            record=repository.get(self._db, table=DBUser, id=id),
            updates=model_update.model_dump(exclude_unset=True),
        )
        return UserRead.model_validate(user_record)

    def delete(self, id: uuid.UUID) -> None:
        """Deletes a user record from the database by its primary key.

        Args:
            id: The UUID identifier of the user record to remove.

        Raises:
            NotFoundError: If no user record matches the provided UUID.
        """
        repository.get(self._db, table=DBUser, id=id)  # Check if the record exists
        repository.delete(self._db, table=DBUser, id=id)
