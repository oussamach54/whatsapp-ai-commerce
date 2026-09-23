"""Application enums persisted as native PostgreSQL enum types."""

from enum import StrEnum

from sqlalchemy import Enum


def postgres_enum(enum_class: type[StrEnum], name: str) -> Enum:
    """Persist StrEnum values, rather than Python member names, in PostgreSQL."""
    return Enum(
        enum_class,
        name=name,
        values_callable=lambda members: [member.value for member in members],
    )


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class PaymentMethod(StrEnum):
    COD = "cod"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class ConversationChannel(StrEnum):
    WHATSAPP = "whatsapp"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    WAITING_HUMAN = "waiting_human"
    CLOSED = "closed"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class SenderType(StrEnum):
    CUSTOMER = "customer"
    AI = "ai"
    HUMAN = "human"
    SYSTEM = "system"


class MessageType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    DOCUMENT = "document"
    INTERACTIVE = "interactive"
