from decimal import Decimal
from uuid import UUID
from pydantic import Field
from app.schemas.common import Money, Quantity, Schema, TimestampRead

from app.models.enums import OrderStatus, PaymentMethod, PaymentStatus
from app.schemas.customer import CustomerRead

class OrderItemCreate(Schema):
    product_variant_id: UUID
    quantity: Quantity

class OrderCreate(Schema):
    customer_id: UUID
    items: list[OrderItemCreate] = Field(min_length=1, max_length=100)
    payment_method: PaymentMethod = PaymentMethod.COD
    payment_status: PaymentStatus = PaymentStatus.PENDING
    currency: str = Field(min_length=3, max_length=3)
    shipping_cost: Money = Decimal("0.00")
    customer_notes: str | None = None
    internal_notes: str | None = None
    shipping_full_name: str = Field(min_length=1, max_length=255)
    shipping_phone_number: str = Field(min_length=1, max_length=32)
    shipping_address_line: str = Field(min_length=1, max_length=500)
    shipping_city: str = Field(min_length=1, max_length=255)
    shipping_postal_code: str | None = Field(default=None, max_length=32)
    shipping_country: str = Field(min_length=2, max_length=2)

class OrderItemRead(TimestampRead):
    order_id: UUID
    product_variant_id: UUID
    product_name_snapshot: str
    variant_name_snapshot: str
    sku_snapshot: str
    unit_price: Money
    quantity: int
    line_total: Money

class OrderRead(OrderCreate, TimestampRead):
    items: list[OrderItemRead]
    customer: CustomerRead
    order_number: str
    subtotal: Money
    total: Money
    status: OrderStatus

class OrderStatusUpdate(Schema):
    status: OrderStatus

class PaymentStatusUpdate(Schema):
    payment_status: PaymentStatus
