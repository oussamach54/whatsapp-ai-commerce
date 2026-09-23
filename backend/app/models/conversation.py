from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDTimestampMixin
from app.models.enums import (
    ConversationChannel,
    ConversationStatus,
    MessageDirection,
    MessageType,
    SenderType,
    postgres_enum,
)

if TYPE_CHECKING:
    from app.models.customer import Customer


class Conversation(UUIDTimestampMixin, Base):
    __tablename__ = "conversations"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    channel: Mapped[ConversationChannel] = mapped_column(
        postgres_enum(ConversationChannel, "conversation_channel"), nullable=False
    )
    status: Mapped[ConversationStatus] = mapped_column(
        postgres_enum(ConversationStatus, "conversation_status"), nullable=False
    )
    assigned_to: Mapped[str | None] = mapped_column(String(255))

    customer: Mapped[Customer] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[MessageDirection] = mapped_column(
        postgres_enum(MessageDirection, "message_direction"), nullable=False
    )
    sender_type: Mapped[SenderType] = mapped_column(postgres_enum(SenderType, "sender_type"), nullable=False)
    message_type: Mapped[MessageType] = mapped_column(
        postgres_enum(MessageType, "message_type"), nullable=False
    )
    content: Mapped[str | None] = mapped_column(Text)
    external_message_id: Mapped[str | None] = mapped_column(String(255), index=True, unique=True)
    metadata_: Mapped[dict[str, object] | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
