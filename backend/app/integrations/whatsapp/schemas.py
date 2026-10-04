from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

class IncomingImage(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str = Field(min_length=1, max_length=255, pattern=r"^[0-9]+$")
    mime_type: str | None = Field(default=None, max_length=100)

class IncomingText(BaseModel):
    model_config = ConfigDict(strict=True)
    external_message_id: str = Field(min_length=1, max_length=255)
    phone_number: str = Field(min_length=1, max_length=32, pattern=r"^\+?[0-9]+$")
    text: str = Field(min_length=1, max_length=4096)
    image: IncomingImage | None = None
    timestamp: datetime | None = None
    phone_number_id: str

class ReplyTarget(BaseModel):
    conversation_id: UUID
    inbound_id: UUID
    phone_number: str
