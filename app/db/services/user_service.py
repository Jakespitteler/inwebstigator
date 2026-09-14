import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db import repository
from app.db.errors import NotFoundError
from app.db.models.user_models import UserCreate, UserRead, UserUpdate
from app.db.schema import DBUser
from app.db.utils.interfaces import CRUDService

logger: logging.Logger = logging.getLogger(__name__)


class UserService(CRUDService[UserRead, UserCreate, UserUpdate]):
    def __init__(self, session: Session):
        """_summary_

        Args:
            session (Session): The database session.
        """
        self._db = session

    def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[UserRead]:
        """
        Retrieves user records.

        Args:
            skip: The number of records to skip.
            limit: The maximum number of records to return.

        Returns:
            The retrieved users.
        """
        user_records: Sequence[DBUser] = repository.get_list(self._db, table=DBUser, skip=skip, limit=limit)
        return [UserRead.model_validate(user_record) for user_record in user_records]

    def get(self, id: uuid.UUID) -> UserRead:
        """
        Retrieves a single user by its primary key.

        Args:
            id: The id of the user to retrieve.

        Raises:
            NotFoundError: If no user exists with the provided ID.

        Returns:
            The retrieved user.
        """
        user_record: DBUser = repository.get(
            self._db,
            table=DBUser,
            id=id,
        )
        return UserRead.model_validate(user_record)

    def get_by_url(self, url: str) -> UserRead:
        """
        Retrieve a user and its relationships by URL.

        Raises:
            NotFoundError: If no user exists with the URL.
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
        """
        Creates a new user record.

        Args:
            model_create: The user details to create.

        Raises:
            IntegrityError: If the user already exists in db.

        Returns:
            The user record.
        """
        user_record: DBUser = DBUser(**model_create.model_dump(exclude={"critical_pages", "internal_links"}))
        repository.add(self._db, record=user_record)

        return UserRead.model_validate(user_record)

    def update(self, id: uuid.UUID, model_update: UserUpdate) -> UserRead:
        """
        Updates an existing user record.

        Args:
            id: The id of the user to update.
            model_update: The new data to apply to the user.

        Raises:
            NotFoundError: If the user with id does not exist.
            IntegrityError: If the user updated details already exists in db.

        Returns:
            The updated user.
        """

        user_record = repository.update(
            self._db,
            record=repository.get(self._db, table=DBUser, id=id),
            updates=model_update.model_dump(exclude_unset=True),
        )
        return UserRead.model_validate(user_record)

    def delete(self, id: uuid.UUID) -> None:
        """
        Deletes a user by its primary key.

        Args:
            id: The id of the user to delete.

        Raises:
            NotFoundError: If no user exists with the provided ID.
        """
        repository.get(self._db, table=DBUser, id=id)  # Check if the record exists
        repository.delete(self._db, table=DBUser, id=id)
