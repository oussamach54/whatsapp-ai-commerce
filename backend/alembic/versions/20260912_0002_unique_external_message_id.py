"""Enforce external message idempotency without changing message columns.

Revision ID: 20260912_0002
Revises: 20260910_0001
"""
from alembic import op

revision = "20260912_0002"
down_revision = "20260910_0001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Existing duplicate non-null IDs fail safely; never delete message data.
    op.drop_index("ix_messages_external_message_id", table_name="messages")
    op.create_index("ix_messages_external_message_id", "messages", ["external_message_id"], unique=True)

def downgrade() -> None:
    op.drop_index("ix_messages_external_message_id", table_name="messages")
    op.create_index("ix_messages_external_message_id", "messages", ["external_message_id"], unique=False)
