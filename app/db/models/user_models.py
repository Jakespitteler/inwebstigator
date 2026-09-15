import uuid

from pydantic import BaseModel, ConfigDict

from app.db.utils.field_types import EmailString


# TODO: internal links and critical pages are not currently linked to user so deletion will delete for all users
# Deleting a website based on its url can delete them for multiple users
class UserCreate(BaseModel):
    email: EmailString
    password: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: EmailString
    password: str


class UserUpdate(BaseModel):
    email: EmailString | None = None
