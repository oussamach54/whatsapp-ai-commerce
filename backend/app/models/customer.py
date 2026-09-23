from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDTimestampMixin

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.order import Order


class Customer(UUIDTimestampMixin, Base):
    __tablename__ = "customers"

    phone_number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(320))
    preferred_language: Mapped[str | None] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    orders: Mapped[list[Order]] = relationship(back_populates="customer")
    conversations: Mapped[list[Conversation]] = relationship(back_populates="customer")
