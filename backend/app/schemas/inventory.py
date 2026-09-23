from pydantic import model_validator
from datetime import datetime
from uuid import UUID
from pydantic import Field
from app.schemas.common import Schema

class InventoryMovementCreate(Schema):
    product_variant_id: UUID
    quantity_change: int = Field(strict=True, ge=-2147483648, le=2147483647)
    reason: str = Field(min_length=1, max_length=100)
    reference_type: str | None = Field(default=None, max_length=100)
    reference_id: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def nonzero(self) -> "InventoryMovementCreate":
        if self.quantity_change == 0:
            raise ValueError("quantity_change must be nonzero")
        return self

class InventoryMovementRead(InventoryMovementCreate):
    id: UUID
    created_at: datetime
