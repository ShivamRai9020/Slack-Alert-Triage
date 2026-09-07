"""add source_channels table for channel-to-vertical identification

Revision ID: 0005_source_channels
Revises: 0004_channel_name
Create Date: 2026-08-14

"""
from alembic import op
import sqlalchemy as sa

revision = "0005_source_channels"
down_revision = "0004_channel_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_channels",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("channel_id", sa.String, nullable=False, unique=True),
        sa.Column("channel_name", sa.String),
        sa.Column("vertical", sa.String, nullable=False),
        sa.Column("service", sa.String),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_source_channels_channel_id", "source_channels", ["channel_id"])


def downgrade() -> None:
    op.drop_table("source_channels")
