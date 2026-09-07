"""add llm_explanation to recommendations_log

Revision ID: 0002_llm_explanation
Revises: 0001_initial
Create Date: 2026-08-13

"""
from alembic import op
import sqlalchemy as sa

revision = "0002_llm_explanation"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recommendations_log", sa.Column("llm_explanation", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("recommendations_log", "llm_explanation")
