from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

class IncomingText(BaseModel):
    model_config = ConfigDict(strict=True)
    external_message_id: str = Field(min_length=1, max_length=255)
    phone_number: str = Field(min_length=1, max_length=32, pattern=r"^\+?[0-9]+$")
    text: str = Field(min_length=1, max_length=4096)
    timestamp: datetime | None = None
    phone_number_id: str

class ReplyTarget(BaseModel):
    conversation_id: UUID
    inbound_id: UUID
    phone_number: str
