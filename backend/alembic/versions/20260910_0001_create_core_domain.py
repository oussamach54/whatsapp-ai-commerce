"""create core database domain

Revision ID: 20260910_0001
Revises:
Create Date: 2026-09-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260910_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


order_status = postgresql.ENUM("pending", "confirmed", "processing", "shipped", "delivered", "cancelled", name="order_status", create_type=False)
payment_method = postgresql.ENUM("cod", name="payment_method", create_type=False)
payment_status = postgresql.ENUM("pending", "paid", "failed", "refunded", name="payment_status", create_type=False)
conversation_channel = postgresql.ENUM("whatsapp", name="conversation_channel", create_type=False)
conversation_status = postgresql.ENUM("active", "waiting_human", "closed", name="conversation_status", create_type=False)
message_direction = postgresql.ENUM("inbound", "outbound", name="message_direction", create_type=False)
sender_type = postgresql.ENUM("customer", "ai", "human", "system", name="sender_type", create_type=False)
message_type = postgresql.ENUM("text", "image", "audio", "document", "interactive", name="message_type", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (
        order_status, payment_method, payment_status, conversation_channel,
        conversation_status, message_direction, sender_type, message_type,
    ):
        enum_type.create(bind, checkfirst=True)

    op.execute("CREATE SEQUENCE order_number_sequence START WITH 1")
    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column("first_name", sa.String(length=100)),
        sa.Column("last_name", sa.String(length=100)),
        sa.Column("email", sa.String(length=320)),
        sa.Column("preferred_language", sa.String(length=16)),
        sa.Column("notes", sa.Text()),
        sa.Column("is_blocked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_customers_phone_number", "customers", ["phone_number"], unique=True)
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text()), sa.Column("brand", sa.String(length=255)),
        sa.Column("category", sa.String(length=255)),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_products_slug", "products", ["slug"], unique=True)
    op.create_table(
        "product_variants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=False), sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False), sa.Column("compare_at_price", sa.Numeric(12, 2)),
        sa.Column("stock_quantity", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("price >= 0", name="ck_product_variants_price_non_negative"),
        sa.CheckConstraint("compare_at_price IS NULL OR compare_at_price >= 0", name="ck_product_variants_compare_at_price_non_negative"),
        sa.CheckConstraint("stock_quantity >= 0", name="ck_product_variants_stock_non_negative"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_variants_product_id", "product_variants", ["product_id"])
    op.create_index("ix_product_variants_sku", "product_variants", ["sku"], unique=True)
    op.create_table(
        "product_images",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("variant_id", sa.Uuid()), sa.Column("url", sa.Text(), nullable=False),
        sa.Column("alt_text", sa.String(length=255)), sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_images_product_id", "product_images", ["product_id"])
    op.create_index("ix_product_images_variant_id", "product_images", ["variant_id"])
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_number", sa.String(length=32), server_default=sa.text("'ORD-' || to_char(CURRENT_DATE, 'YYYY') || '-' || lpad(nextval('order_number_sequence')::text, 6, '0')"), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False), sa.Column("status", order_status, nullable=False),
        sa.Column("payment_method", payment_method, nullable=False), sa.Column("payment_status", payment_status, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False), sa.Column("subtotal", sa.Numeric(12, 2), nullable=False),
        sa.Column("shipping_cost", sa.Numeric(12, 2), nullable=False), sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column("customer_notes", sa.Text()), sa.Column("internal_notes", sa.Text()),
        sa.Column("shipping_full_name", sa.String(length=255), nullable=False), sa.Column("shipping_phone_number", sa.String(length=32), nullable=False),
        sa.Column("shipping_address_line", sa.String(length=500), nullable=False), sa.Column("shipping_city", sa.String(length=255), nullable=False),
        sa.Column("shipping_postal_code", sa.String(length=32)), sa.Column("shipping_country", sa.String(length=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("subtotal >= 0", name="ck_orders_subtotal_non_negative"), sa.CheckConstraint("shipping_cost >= 0", name="ck_orders_shipping_cost_non_negative"), sa.CheckConstraint("total >= 0", name="ck_orders_total_non_negative"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])
    op.create_index("ix_orders_order_number", "orders", ["order_number"], unique=True)
    op.create_table(
        "order_items",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("order_id", sa.Uuid(), nullable=False), sa.Column("product_variant_id", sa.Uuid(), nullable=False),
        sa.Column("product_name_snapshot", sa.String(length=255), nullable=False), sa.Column("variant_name_snapshot", sa.String(length=255), nullable=False), sa.Column("sku_snapshot", sa.String(length=100), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False), sa.Column("quantity", sa.Integer(), nullable=False), sa.Column("line_total", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("unit_price >= 0", name="ck_order_items_unit_price_non_negative"), sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"), sa.CheckConstraint("line_total >= 0", name="ck_order_items_line_total_non_negative"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["product_variant_id"], ["product_variants.id"], ondelete="RESTRICT"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_product_variant_id", "order_items", ["product_variant_id"])
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("customer_id", sa.Uuid(), nullable=False), sa.Column("channel", conversation_channel, nullable=False), sa.Column("status", conversation_status, nullable=False), sa.Column("assigned_to", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_customer_id", "conversations", ["customer_id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("conversation_id", sa.Uuid(), nullable=False), sa.Column("direction", message_direction, nullable=False), sa.Column("sender_type", sender_type, nullable=False), sa.Column("message_type", message_type, nullable=False),
        sa.Column("content", sa.Text()), sa.Column("external_message_id", sa.String(length=255)), sa.Column("metadata", postgresql.JSONB()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_external_message_id", "messages", ["external_message_id"])
    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("product_variant_id", sa.Uuid(), nullable=False), sa.Column("quantity_change", sa.Integer(), nullable=False), sa.Column("reason", sa.String(length=100), nullable=False), sa.Column("reference_type", sa.String(length=100)), sa.Column("reference_id", sa.String(length=255)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("quantity_change <> 0", name="ck_inventory_movements_quantity_change_non_zero"), sa.ForeignKeyConstraint(["product_variant_id"], ["product_variants.id"], ondelete="RESTRICT"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inventory_movements_product_variant_id", "inventory_movements", ["product_variant_id"])


def downgrade() -> None:
    op.drop_table("inventory_movements")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("product_images")
    op.drop_table("product_variants")
    op.drop_table("products")
    op.drop_table("customers")
    op.execute("DROP SEQUENCE order_number_sequence")
    bind = op.get_bind()
    for enum_type in (message_type, sender_type, message_direction, conversation_status, conversation_channel, payment_status, payment_method, order_status):
        enum_type.drop(bind, checkfirst=True)
