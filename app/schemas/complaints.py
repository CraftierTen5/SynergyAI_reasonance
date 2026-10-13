from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceChannel(str, Enum):
    WEB_PORTAL = "Web portal"
    PHONE_HELPLINE = "Phone helpline"
    SOCIAL_MEDIA = "Social media"
    MUNICIPAL_OFFICE = "In-person municipal office"


class ComplaintInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_channel: SourceChannel
    original_text: str = Field(min_length=1, max_length=10000)
    language_code: str = Field(pattern=r"^(en|hi|hinglish)$")
    location_text: str | None = Field(default=None, max_length=1000)
    ward_id: str | None = Field(default=None, max_length=50)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    submitted_at: datetime
    idempotency_key: str | None = Field(default=None, max_length=200)

    @field_validator("submitted_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("submitted_at must include a timezone")
        return value


class BulkInput(BaseModel):
    complaints: list[dict[str, Any]] = Field(min_length=1, max_length=1000)

