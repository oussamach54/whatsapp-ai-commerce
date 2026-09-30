from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIBudget(Base):
    """Shared fixed-window counters and bounded rolling spam/cooldown state."""
    __tablename__ = "ai_budget_counters"
    __table_args__ = (CheckConstraint("count >= 0", name="ck_ai_budget_count"),)

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    state: Mapped[dict | None] = mapped_column(JSONB)
