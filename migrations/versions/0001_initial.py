"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-12

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "alert_fingerprints",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("fingerprint_hash", sa.String, nullable=False, unique=True),
        sa.Column("alert_name", sa.String),
        sa.Column("service", sa.String),
        sa.Column("vertical", sa.String),
        sa.Column("normalized_labels", JSON_TYPE),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_classification", sa.String),
        sa.Column("last_notified_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_alert_fingerprints_hash", "alert_fingerprints", ["fingerprint_hash"])

    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("fingerprint_id", sa.Integer, sa.ForeignKey("alert_fingerprints.id")),
        sa.Column("source_channel", sa.String, nullable=False),
        sa.Column("raw_message", sa.Text, nullable=False),
        sa.Column("alert_name", sa.String),
        sa.Column("service", sa.String),
        sa.Column("vertical", sa.String),
        sa.Column("environment", sa.String),
        sa.Column("severity", sa.String),
        sa.Column("host_or_pod", sa.String),
        sa.Column("labels", JSON_TYPE),
        sa.Column("slack_ts", sa.String),
        sa.Column("slack_thread_ts", sa.String),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_alert_events_fingerprint_id", "alert_events", ["fingerprint_id"])
    op.create_index("ix_alert_events_occurred_at", "alert_events", ["occurred_at"])

    op.create_table(
        "vertical_config",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("vertical", sa.String, nullable=False),
        sa.Column("service", sa.String),
        sa.Column("alert_name", sa.String),
        sa.Column("alert_channel", sa.String, nullable=False),
        sa.Column("dev_notify_channel", sa.String),
        sa.Column("devops_notify_channel", sa.String),
        sa.Column("business_hours_tz", sa.String, server_default="UTC"),
        sa.Column("business_hours_start", sa.Time),
        sa.Column("business_hours_end", sa.Time),
        sa.UniqueConstraint("vertical", "service", "alert_name", name="uq_vertical_service_alert"),
    )

    op.create_table(
        "triage_rules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("rule_name", sa.String, nullable=False),
        sa.Column("classification", sa.String, nullable=False),
        sa.Column("condition_json", JSON_TYPE, nullable=False),
        sa.Column("priority", sa.Integer, server_default="100"),
        sa.Column("active", sa.Boolean, server_default=sa.true()),
    )

    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("fingerprint_id", sa.Integer, sa.ForeignKey("alert_fingerprints.id")),
        sa.Column("jira_issue_key", sa.String),
        sa.Column("jira_url", sa.String),
        sa.Column("status", sa.String, server_default="open"),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("last_linked_alert_event_id", sa.Integer, sa.ForeignKey("alert_events.id")),
        sa.Column("occurrence_count_at_open", sa.Integer, server_default="1"),
    )
    op.create_index("ix_incidents_fingerprint_id", "incidents", ["fingerprint_id"])

    op.create_table(
        "recommendations_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("fingerprint_id", sa.Integer, sa.ForeignKey("alert_fingerprints.id")),
        sa.Column("alert_event_id", sa.Integer, sa.ForeignKey("alert_events.id")),
        sa.Column("classification", sa.String, nullable=False),
        sa.Column("rationale", sa.Text),
        sa.Column("notified_channel", sa.String),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column("human_reaction", sa.String),
    )


def downgrade() -> None:
    op.drop_table("recommendations_log")
    op.drop_table("incidents")
    op.drop_table("triage_rules")
    op.drop_table("vertical_config")
    op.drop_table("alert_events")
    op.drop_table("alert_fingerprints")
