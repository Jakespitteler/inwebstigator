import uuid

from fastapi import APIRouter, Response, status

from app.db.services.tag_service import TagService
from app.frontend.api.db_router_factory import SessionDep
from app.models.tag_models import TagName, TagRead

TAG_ROUTER = APIRouter(tags=["Website tags"])


@TAG_ROUTER.get("/tags", response_model=list[TagRead])
def list_tags(session: SessionDep) -> list[TagRead]:
    return TagService(session).get_all()


@TAG_ROUTER.post("/tags", response_model=TagRead, status_code=status.HTTP_201_CREATED)
def create_tag(model: TagName, session: SessionDep) -> TagRead:
    return TagService(session).save(model)


@TAG_ROUTER.patch("/tags/{tag_id}", response_model=TagRead)
def rename_tag(tag_id: uuid.UUID, model: TagName, session: SessionDep) -> TagRead:
    return TagService(session).save(model, tag_id)


@TAG_ROUTER.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(tag_id: uuid.UUID, session: SessionDep) -> Response:
    TagService(session).delete(tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@TAG_ROUTER.get("/websites/{website_id}/tags", response_model=list[TagRead])
def list_website_tags(website_id: uuid.UUID, session: SessionDep) -> list[TagRead]:
    return TagService(session).get_for_website(website_id)


@TAG_ROUTER.post("/websites/{website_id}/tags/{tag_id}", response_model=list[TagRead])
def add_website_tag(website_id: uuid.UUID, tag_id: uuid.UUID, session: SessionDep) -> list[TagRead]:
    service = TagService(session)
    service.set_assignment(website_id, tag_id, assigned=True)
    return service.get_for_website(website_id)


@TAG_ROUTER.delete("/websites/{website_id}/tags/{tag_id}", response_model=list[TagRead])
def remove_website_tag(website_id: uuid.UUID, tag_id: uuid.UUID, session: SessionDep) -> list[TagRead]:
    service = TagService(session)
    service.set_assignment(website_id, tag_id, assigned=False)
    return service.get_for_website(website_id)
