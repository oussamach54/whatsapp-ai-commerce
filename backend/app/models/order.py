from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDTimestampMixin
from app.models.enums import OrderStatus, PaymentMethod, PaymentStatus, postgres_enum

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.product import ProductVariant


class Order(UUIDTimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("subtotal >= 0", name="ck_orders_subtotal_non_negative"),
        CheckConstraint("shipping_cost >= 0", name="ck_orders_shipping_cost_non_negative"),
        CheckConstraint("total >= 0", name="ck_orders_total_non_negative"),
    )

    order_number: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        index=True,
        nullable=False,
        server_default=text(
            "'ORD-' || to_char(CURRENT_DATE, 'YYYY') || '-' || "
            "lpad(nextval('order_number_sequence')::text, 6, '0')"
        ),
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[OrderStatus] = mapped_column(postgres_enum(OrderStatus, "order_status"), nullable=False)
    payment_method: Mapped[PaymentMethod] = mapped_column(
        postgres_enum(PaymentMethod, "payment_method"), nullable=False
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        postgres_enum(PaymentStatus, "payment_status"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    shipping_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    customer_notes: Mapped[str | None] = mapped_column(Text)
    internal_notes: Mapped[str | None] = mapped_column(Text)
    shipping_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    shipping_phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    shipping_address_line: Mapped[str] = mapped_column(String(500), nullable=False)
    shipping_city: Mapped[str] = mapped_column(String(255), nullable=False)
    shipping_postal_code: Mapped[str | None] = mapped_column(String(32))
    shipping_country: Mapped[str] = mapped_column(String(2), nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(back_populates="order")


class OrderItem(UUIDTimestampMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("unit_price >= 0", name="ck_order_items_unit_price_non_negative"),
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        CheckConstraint("line_total >= 0", name="ck_order_items_line_total_non_negative"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    variant_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    sku_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    product_variant: Mapped[ProductVariant] = relationship(back_populates="order_items")
