import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr

from app.core.config import config

DEFAULT_DAYS_BETWEEN_HEALTH_CHECKS: float = config.scheduler_default_days_between_health_checks


class RecipientCreate(BaseModel):
    email: EmailStr
    days_between_health_checks: float = DEFAULT_DAYS_BETWEEN_HEALTH_CHECKS


class RecipientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: EmailStr
    last_email_at: datetime | None
    days_between_health_checks: float
    created_at: datetime


class RecipientUpdate(BaseModel):
    email: EmailStr | None = None
    last_email_at: AwareDatetime | None = None  # A time without a zone is refused, as it cannot be saved as UTC
    days_between_health_checks: float | None = None
