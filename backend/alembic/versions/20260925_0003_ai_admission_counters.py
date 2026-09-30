"""Shared AI admission counters; no changes to conversation memory.

Revision ID: 20260925_0003
Revises: 20260912_0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260925_0003"
down_revision = "20260912_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ai_budget_counters",
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("key", "window_start"),
        sa.CheckConstraint("count >= 0", name="ck_ai_budget_count"))
    op.create_index("ix_ai_budget_counters_expires_at", "ai_budget_counters", ["expires_at"])


def downgrade():
    op.drop_index("ix_ai_budget_counters_expires_at", table_name="ai_budget_counters")
    op.drop_table("ai_budget_counters")
