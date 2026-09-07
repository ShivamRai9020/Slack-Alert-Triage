"""add channel_name for channel-based vertical identification

Revision ID: 0004_channel_name
Revises: 0003_alert_priority
Create Date: 2026-08-14

"""
from alembic import op
import sqlalchemy as sa

revision = "0004_channel_name"
down_revision = "0003_alert_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vertical_config", sa.Column("channel_name", sa.String, nullable=True))
    op.add_column("alert_events", sa.Column("source_channel_name", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("alert_events", "source_channel_name")
    op.drop_column("vertical_config", "channel_name")
