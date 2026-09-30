"""Application-owned purchase state. Snapshots are never current catalog authority."""
from datetime import datetime
from uuid import UUID, uuid4
from typing import Literal
from pydantic import Field
from app.ai.catalog_schemas import Contract, ProductRef, CheckoutField


class CartLine(Contract):
    target: ProductRef
    quantity: int = Field(ge=1, le=99)
    unit_price: str = Field(pattern=r"^\d{1,10}(\.\d{1,2})?$")
    stock: int = Field(ge=0)
    product_name: str = Field(max_length=255)
    variant_name: str = Field(max_length=255)


class CustomerValue(Contract):
    value: str = Field(min_length=1, max_length=500)
    source: Literal["channel", "customer", "profile"]
    message_id: UUID | None = None


class Cart(Contract):
    id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    items: list[CartLine] = Field(default_factory=list, max_length=6)
    status: Literal["awaiting_confirmation", "confirmed", "completed", "cancelled", "blocked"] = "awaiting_confirmation"
    fields: dict[CheckoutField, CustomerValue] = Field(default_factory=dict, max_length=6)
    subtotal: str = "0.00"
    shipping_cost: str | None = None
    total: str | None = None
    offered_at: datetime
    confirmed_version: int | None = None
    confirmation_message_id: UUID | None = None
    order_id: UUID | None = None
    order_number: str | None = None
