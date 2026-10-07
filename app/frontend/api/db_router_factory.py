import uuid
from collections.abc import Sequence, Set
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.services.crud_protocol import CRUDOperation, CRUDService
from app.db.session import get_db_session

SessionDep = Annotated[Session, Depends(get_db_session)]


def create_crud_router[READ: BaseModel, CREATE: BaseModel, UPDATE: BaseModel](
    prefix: str,
    service_class: type[CRUDService[READ, CREATE, UPDATE]],
    create_class: type[CREATE],
    update_class: type[UPDATE],
    exclude: Set[CRUDOperation] = frozenset(),
) -> APIRouter:
    """Generates a standardised CRUD router for Database Operations.

    Args:
        prefix: The URL prefix of the router's routes, e.g. "/websites".
        service_class: The database service that carries out each operation.
        create_class: The model accepted when creating a record.
        update_class: The model accepted when updating a record.
        exclude: Operations left out, so a route with extra behaviour can be added to the router in their place.
            Defaults to none.

    Returns:
        The router, with a route for each operation not excluded.
    """
    router = APIRouter(prefix=prefix, tags=[prefix.split("/")[-1].capitalize()])

    if CRUDOperation.GET_ALL not in exclude:

        @router.get("/", response_model=Sequence[READ], status_code=status.HTTP_200_OK)
        def get_items(session: SessionDep, skip: int = 0, limit: int = 100) -> Sequence[READ]:
            return service_class(session).get_all(skip, limit)

    if CRUDOperation.GET not in exclude:

        @router.get("/{id}", response_model=READ, status_code=status.HTTP_200_OK)
        def get_item(session: SessionDep, id: uuid.UUID) -> READ:
            return service_class(session).get(id)

    if CRUDOperation.CREATE not in exclude:

        @router.post("/", response_model=READ, status_code=status.HTTP_201_CREATED)
        def create_item(session: SessionDep, item_in: create_class) -> READ:  # type: ignore
            return service_class(session).create(item_in)  # type: ignore

    if CRUDOperation.UPDATE not in exclude:

        @router.patch("/{id}", response_model=READ, status_code=status.HTTP_200_OK)
        def update_item(session: SessionDep, id: uuid.UUID, item_in: update_class) -> READ:  # type: ignore
            return service_class(session).update(id, item_in)  # type: ignore

    if CRUDOperation.DELETE not in exclude:

        @router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_item(session: SessionDep, id: uuid.UUID) -> None:
            service_class(session).delete(id)

    return router
