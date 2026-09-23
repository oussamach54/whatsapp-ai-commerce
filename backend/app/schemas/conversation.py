from datetime import datetime
from uuid import UUID
from pydantic import Field
from app.schemas.common import Schema, TimestampRead

from pydantic import AliasChoices
from app.models.enums import ConversationChannel, ConversationStatus, MessageDirection, SenderType, MessageType
from app.schemas.customer import CustomerRead

class ConversationCreate(Schema):
    customer_id: UUID
    channel: ConversationChannel = ConversationChannel.WHATSAPP
    status: ConversationStatus = ConversationStatus.ACTIVE
    assigned_to: str | None = Field(default=None, max_length=255)

class MessageCreate(Schema):
    direction: MessageDirection
    sender_type: SenderType
    message_type: MessageType = MessageType.TEXT
    content: str | None = None
    external_message_id: str | None = Field(default=None, max_length=255)
    metadata: dict[str, object] | None = None

class MessageRead(MessageCreate):
    id: UUID
    conversation_id: UUID
    created_at: datetime
    metadata: dict[str, object] | None = Field(default=None, validation_alias=AliasChoices("metadata_", "metadata"))

class ConversationRead(ConversationCreate, TimestampRead):
    customer: CustomerRead
