"""add priority to alert_events

Revision ID: 0003_alert_priority
Revises: 0002_llm_explanation
Create Date: 2026-08-14

"""
from alembic import op
import sqlalchemy as sa

revision = "0003_alert_priority"
down_revision = "0002_llm_explanation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alert_events", sa.Column("priority", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("alert_events", "priority")
