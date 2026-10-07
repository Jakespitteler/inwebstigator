import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.config import config
from app.models.field_types import EmailString

DEFAULT_DAYS_BETWEEN_HEALTH_CHECKS: float = config.scheduler_default_days_between_health_checks


class RecipientCreate(BaseModel):
    email: EmailString
    days_between_health_checks: float = DEFAULT_DAYS_BETWEEN_HEALTH_CHECKS


class RecipientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: EmailString
    last_email_at: datetime | None
    days_between_health_checks: float
    created_at: datetime


class RecipientUpdate(BaseModel):
    email: EmailString | None = None
    last_email_at: datetime | None = None
    days_between_health_checks: float | None = None
