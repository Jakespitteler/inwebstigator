import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.utils.field_types import EmailString
from app.models.website_models import WebsiteRead


class UserCreate(BaseModel):
    email: EmailString
    password: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: EmailString
    password: str
    days_between_scans: int
    days_between_heath_checks: int
    last_scan_at: datetime | None = None
    last_email_at: datetime | None = None
    websites: list[WebsiteRead]


class UserUpdate(BaseModel):
    email: EmailString | None = None
    password: str | None = None
    days_between_scans: int | None = None
    days_between_heath_checks: int | None = None
    last_scan_at: datetime | None = None
    last_email_at: datetime | None = None
