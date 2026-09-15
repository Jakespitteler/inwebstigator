from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.schema import DBUser
from app.db.services.user_service import UserService
from app.models.user_models import UserCreate, UserRead, UserUpdate


def test_get_all_users(session: Session, test_user: DBUser) -> None:
    """
    Tests retrieving a list of all users.

    Args:
        session: The database session fixture.
        test_user: The test user record.
    """
    fetched_users: Sequence[UserRead] = UserService(session).get_all()
    assert len(fetched_users) >= 1
    assert any(c.id == test_user.id for c in fetched_users)
    assert any(c.email == test_user.email for c in fetched_users)


def test_get_user(session: Session, test_user: DBUser) -> None:
    """
    Tests retrieving an existing user by ID.

    Args:
        session: The database session fixture.
        test_user: The test user record.
    """
    fetched_user: UserRead = UserService(session).get(id=test_user.id)

    assert fetched_user is not None
    assert fetched_user.id == test_user.id
    assert fetched_user.email == test_user.email


def test_create_user(session: Session) -> None:
    """
    Tests creating a new user with basic details.

    Args:
        session: The database session fixture.
    """
    user_details = UserCreate(email="test_user@email.com", password="")

    created_user: UserRead = UserService(session).create(user_details)
    assert created_user.id is not None
    assert created_user.email == user_details.email

    fetched_user: UserRead = UserService(session).get(id=created_user.id)
    assert fetched_user.email == user_details.email


def test_update_user(session: Session, test_user: DBUser) -> None:
    """
    Tests updating an existing user's details.

    Args:
        session: The database session fixture.
        test_user: The test user record.
    """
    model_update = UserUpdate(email="updated_user@email.com")

    updated_user: UserRead = UserService(session).update(id=test_user.id, model_update=model_update)
    assert updated_user.id == test_user.id
    assert updated_user.email == model_update.email

    fetched_user: UserRead = UserService(session).get(id=test_user.id)
    assert fetched_user.email == model_update.email


def test_delete_user(session: Session, test_user: DBUser) -> None:
    """
    Tests deleting an existing user and cascading its children.

    Args:
        session: The database session fixture.
        test_user: The test user record.
    """
    UserService(session).delete(id=test_user.id)

    # Check the user can no longer be retrieved
    with pytest.raises(NotFoundError):
        UserService(session).get(id=test_user.id)
