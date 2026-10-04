"""Application-owned cancellation checkpoint; never populated by a provider."""
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4
from pydantic import Field
from app.ai.catalog_schemas import Contract


class PendingCancellation(Contract):
    action: Literal["order_cancellation"] = "order_cancellation"
    id: UUID = Field(default_factory=uuid4)
    customer_id: UUID
    conversation_id: UUID
    order_ids: list[UUID] = Field(max_length=3)
    request_message_id: UUID
    offered_at: datetime
    status: Literal["choosing", "awaiting_confirmation", "cancelled", "declined", "superseded"]
    confirmation_message_id: UUID | None = None

    def marker(self):
        return {"action": self.action, "id": str(self.id), "order_id": str(self.order_ids[0])}
