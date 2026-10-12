import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError as SQLIntegrityError
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, TagAlreadyExistsError
from app.db.schema import DBWebsite, DBWebsiteTag, website_tag_association
from app.models.tag_models import TagName, TagRead


class TagService:
    """Saves labels independently of scan settings, history and notification emails.

    The caller owns the transaction, as with the other database services.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_all(self) -> list[TagRead]:
        return [
            TagRead.model_validate(tag)
            for tag in self.session.scalars(select(DBWebsiteTag).order_by(DBWebsiteTag.name_key))
        ]

    def memberships(self) -> dict[uuid.UUID, list[uuid.UUID]]:
        assignments: dict[uuid.UUID, list[uuid.UUID]] = {}
        for website_id, tag_id in self.session.execute(
            select(
                website_tag_association.c.website_id,
                website_tag_association.c.tag_id,
            )
        ):
            assignments.setdefault(website_id, []).append(tag_id)
        return assignments

    def get_for_website(self, website_id: uuid.UUID) -> list[TagRead]:
        if self.session.get(DBWebsite, website_id) is None:
            raise NotFoundError(id=website_id)
        return [
            TagRead.model_validate(tag)
            for tag in self.session.scalars(
                select(DBWebsiteTag)
                .join(website_tag_association)
                .where(
                    website_tag_association.c.website_id == website_id,
                )
                .order_by(DBWebsiteTag.name_key)
            )
        ]

    def _get(self, tag_id: uuid.UUID) -> DBWebsiteTag:
        tag = self.session.get(DBWebsiteTag, tag_id)
        if tag is None:
            raise NotFoundError(id=tag_id)
        return tag

    def save(self, model: TagName, tag_id: uuid.UUID | None = None) -> TagRead:
        tag = self._get(tag_id) if tag_id else DBWebsiteTag()
        name_key = model.name.casefold()
        existing = self.session.scalar(select(DBWebsiteTag).where(DBWebsiteTag.name_key == name_key))
        if existing is not None and existing.id != tag.id:
            raise TagAlreadyExistsError()
        try:
            with self.session.begin_nested():
                tag.name = model.name
                tag.name_key = name_key
                self.session.add(tag)
                self.session.flush()
        except SQLIntegrityError as exc:
            raise TagAlreadyExistsError() from exc
        return TagRead.model_validate(tag)

    def delete(self, tag_id: uuid.UUID) -> None:
        """Remove the tag and its assignments; never delete a website."""
        self.session.delete(self._get(tag_id))
        self.session.flush()

    def set_assignment(self, website_id: uuid.UUID, tag_id: uuid.UUID, assigned: bool) -> None:
        """Change one tag at a time so editing one label cannot overwrite another window's tags."""
        if self.session.get(DBWebsite, website_id) is None:
            raise NotFoundError(id=website_id)
        self._get(tag_id)
        if assigned:
            self.session.execute(
                insert(website_tag_association)
                .values(
                    website_id=website_id,
                    tag_id=tag_id,
                )
                .on_conflict_do_nothing(index_elements=["website_id", "tag_id"])
            )
        else:
            self.session.execute(
                delete(website_tag_association).where(
                    website_tag_association.c.website_id == website_id,
                    website_tag_association.c.tag_id == tag_id,
                )
            )
