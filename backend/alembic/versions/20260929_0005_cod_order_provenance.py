"""COD idempotency and explicitly optional delivery data; no new tables."""
from alembic import op
import sqlalchemy as sa

revision = "20260929_0005"
down_revision = "20260927_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("source_cart_id", sa.Uuid(), nullable=True))
    op.add_column("orders", sa.Column("source_conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=True))
    op.add_column("orders", sa.Column("source_message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="RESTRICT"), nullable=True))
    op.create_unique_constraint("uq_orders_source_cart_id", "orders", ["source_cart_id"])
    for name in ("shipping_full_name", "shipping_address_line", "shipping_city", "shipping_cost", "total"):
        op.alter_column("orders", name, nullable=True)


def downgrade():
    # Refuse a lossy downgrade if optional data has been used.
    for name in ("shipping_full_name", "shipping_address_line", "shipping_city", "shipping_cost", "total"):
        op.alter_column("orders", name, nullable=False)
    op.drop_constraint("uq_orders_source_cart_id", "orders", type_="unique")
    for name in ("source_message_id", "source_conversation_id", "source_cart_id"):
        op.drop_column("orders", name)
