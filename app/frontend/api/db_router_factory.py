import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.core import get_db_session
from app.db.utils.interfaces import CRUDService

SessionDep = Annotated[Session, Depends(get_db_session)]


def create_crud_router[READ: BaseModel, CREATE: BaseModel, UPDATE: BaseModel](
    prefix: str,
    service_class: type[CRUDService[READ, CREATE, UPDATE]],
    create_class: type[CREATE],
    update_class: type[UPDATE],
) -> APIRouter:
    """Generates a standardised CRUD router for Database Operations."""
    router = APIRouter(prefix=prefix, tags=[prefix.split("/")[-1].capitalize()])

    @router.get("/", response_model=Sequence[READ], status_code=status.HTTP_200_OK)
    def get_items(session: SessionDep, skip: int = 0, limit: int = 100) -> Sequence[READ]:
        return service_class(session).get_all(skip, limit)

    @router.get("/{id}", response_model=READ, status_code=status.HTTP_200_OK)
    def get_item(session: SessionDep, id: uuid.UUID) -> READ:
        return service_class(session).get(id)

    @router.post("/", response_model=READ, status_code=status.HTTP_201_CREATED)
    def create_item(session: SessionDep, item_in: create_class) -> READ:  # type: ignore
        return service_class(session).create(item_in)  # type: ignore

    @router.patch("/{id}", response_model=READ, status_code=status.HTTP_200_OK)
    def update_item(session: SessionDep, id: uuid.UUID, item_in: update_class) -> READ:  # type: ignore
        return service_class(session).update(id, item_in)  # type: ignore

    @router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_item(session: SessionDep, id: uuid.UUID) -> None:
        service_class(session).delete(id)

    return router
