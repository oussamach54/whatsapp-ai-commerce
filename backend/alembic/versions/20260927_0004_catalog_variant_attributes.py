"""Nullable structured variant attributes; deliberately no inferred backfill.

Revision ID: 20260927_0004
Revises: 20260925_0003
"""
from alembic import op
import sqlalchemy as sa

revision = "20260927_0004"
down_revision = "20260925_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("product_variants", sa.Column("size", sa.String(64), nullable=True))
    op.add_column("product_variants", sa.Column("color", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("product_variants", "color")
    op.drop_column("product_variants", "size")
