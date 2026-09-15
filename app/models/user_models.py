import uuid

from pydantic import BaseModel, ConfigDict

from app.db.utils.field_types import EmailString


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
