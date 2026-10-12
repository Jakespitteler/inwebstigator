import unicodedata
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TagName(BaseModel):
    name: str = Field(min_length=1, max_length=60)

    @field_validator("name", mode="before")
    @classmethod
    def tidy_name(cls, value: object) -> object:
        """Keep the supplied spelling while treating extra spaces and Unicode variants consistently."""
        if isinstance(value, str):
            return " ".join(unicodedata.normalize("NFKC", value).split())
        return value


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
