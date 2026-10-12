"""add alert_daily_rollups table for alert_events retention

Revision ID: 0006_alert_daily_rollups
Revises: 0005_source_channels
Create Date: 2026-10-12

"""
from alembic import op
import sqlalchemy as sa

revision = "0006_alert_daily_rollups"
down_revision = "0005_source_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_daily_rollups",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("vertical", sa.String, nullable=False, server_default=""),
        sa.Column("source_channel", sa.String, nullable=False, server_default=""),
        sa.Column("source_channel_name", sa.String),
        sa.Column("priority", sa.String, nullable=False, server_default=""),
        sa.Column("severity", sa.String, nullable=False, server_default=""),
        sa.Column("count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "day", "vertical", "source_channel", "priority", "severity",
            name="uq_alert_daily_rollup_bucket",
        ),
    )
    op.create_index("ix_alert_daily_rollups_day", "alert_daily_rollups", ["day"])


def downgrade() -> None:
    op.drop_index("ix_alert_daily_rollups_day", table_name="alert_daily_rollups")
    op.drop_table("alert_daily_rollups")
