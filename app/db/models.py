"""
ORM models. Kept intentionally close to the proposed SQL schema so the
mapping between "what we designed" and "what's running" stays obvious.
"""
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Dialect-aware JSON type: JSONB on PostgreSQL, standard JSON elsewhere
# (e.g. SQLite for local dev/tests) -- JSONB is a Postgres-only type and
# raises at table-creation time on SQLite without this.
JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AlertFingerprint(Base):
    __tablename__ = "alert_fingerprints"

    # Integer + autoincrement=True (not BigInteger) so SQLite maps this to
    # its native rowid-aliased INTEGER PRIMARY KEY and autoincrements
    # correctly for local dev/tests; still a normal serial/identity column
    # under Postgres in production.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    alert_name: Mapped[str | None] = mapped_column(String)
    service: Mapped[str | None] = mapped_column(String)
    vertical: Mapped[str | None] = mapped_column(String)
    normalized_labels: Mapped[dict | None] = mapped_column(JSON_TYPE)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_classification: Mapped[str | None] = mapped_column(String)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list["AlertEvent"]] = relationship(back_populates="fingerprint")


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint_id: Mapped[int] = mapped_column(ForeignKey("alert_fingerprints.id"), index=True)
    source_channel: Mapped[str] = mapped_column(String, nullable=False)
    # Denormalized copy of vertical_config.channel_name AT THE TIME this
    # alert was processed -- kept even if the config is later renamed/
    # deleted, so historical alerts keep an accurate display label. NULL
    # when this channel wasn't registered in vertical_config yet.
    source_channel_name: Mapped[str | None] = mapped_column(String)
    raw_message: Mapped[str] = mapped_column(Text, nullable=False)
    alert_name: Mapped[str | None] = mapped_column(String)
    service: Mapped[str | None] = mapped_column(String)
    vertical: Mapped[str | None] = mapped_column(String)
    environment: Mapped[str | None] = mapped_column(String)
    severity: Mapped[str | None] = mapped_column(String)
    # Explicit priority (e.g. "P0") when the source alert states one --
    # see ParsedAlert.priority for why this is kept distinct from the
    # dashboard's severity-derived fallback.
    priority: Mapped[str | None] = mapped_column(String)
    host_or_pod: Mapped[str | None] = mapped_column(String)
    labels: Mapped[dict | None] = mapped_column(JSON_TYPE)
    slack_ts: Mapped[str | None] = mapped_column(String)
    slack_thread_ts: Mapped[str | None] = mapped_column(String)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    fingerprint: Mapped["AlertFingerprint"] = relationship(back_populates="events")


class VerticalConfig(Base):
    __tablename__ = "vertical_config"
    __table_args__ = (
        UniqueConstraint("vertical", "service", "alert_name", name="uq_vertical_service_alert"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vertical: Mapped[str] = mapped_column(String, nullable=False)
    service: Mapped[str | None] = mapped_column(String)  # null = applies to whole vertical
    # null = applies to all alert types for this vertical/service. Set this
    # when a specific alert type needs different ownership than its
    # service's default (e.g. DB alerts routed to a DBA channel even
    # though the service is otherwise Dev-owned).
    alert_name: Mapped[str | None] = mapped_column(String)
    alert_channel: Mapped[str] = mapped_column(String, nullable=False)
    # DEPRECATED for identification purposes as of the SourceChannel table
    # above -- this couldn't represent "many channels, one vertical" (see
    # SourceChannel's docstring for why) and is no longer read by the
    # pipeline for that purpose. Left in place to avoid a destructive
    # migration; still fine as free-form documentation of "the channel
    # this routing rule was originally set up alongside," just not
    # authoritative for anything anymore.
    channel_name: Mapped[str | None] = mapped_column(String)
    dev_notify_channel: Mapped[str | None] = mapped_column(String)
    devops_notify_channel: Mapped[str | None] = mapped_column(String)
    business_hours_tz: Mapped[str] = mapped_column(String, default="UTC")
    business_hours_start: Mapped[str | None] = mapped_column(Time)
    business_hours_end: Mapped[str | None] = mapped_column(Time)


class TriageRule(Base):
    __tablename__ = "triage_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_name: Mapped[str] = mapped_column(String, nullable=False)
    classification: Mapped[str] = mapped_column(String, nullable=False)
    condition_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SourceChannel(Base):
    """
    Identifies which vertical/service a Slack channel's alerts belong to
    -- the "input" side of channel configuration (which vertical is THIS
    alert from), kept deliberately separate from VerticalConfig (the
    "output" side: for a given vertical/service, WHERE to send
    notifications). These are different concerns with different
    cardinality: many channels can belong to one vertical (e.g. both
    "pg-aws" and "pg-canary" belong to vertical "pg2.0"), which
    VerticalConfig's (vertical, service, alert_name) uniqueness has no
    room to represent when service/alert_name are both null for more
    than one channel. channel_id being the unique key here means that
    case just works: many rows, same vertical, each keyed on a distinct
    channel.
    """
    __tablename__ = "source_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    # Human-readable label (e.g. "pg-aws") for display only -- identification
    # always uses channel_id, never this name.
    channel_name: Mapped[str | None] = mapped_column(String)
    vertical: Mapped[str] = mapped_column(String, nullable=False)
    service: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Incident(Base):
    """
    Links an alert fingerprint to a tracked incident (Jira ticket).
    A fingerprint should have at most one OPEN incident at a time --
    recurrences of the same alert type update the existing incident
    (via a Jira comment) rather than creating a new ticket.
    """
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint_id: Mapped[int] = mapped_column(ForeignKey("alert_fingerprints.id"), index=True)
    jira_issue_key: Mapped[str | None] = mapped_column(String)
    jira_url: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="open")  # open | resolved
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_linked_alert_event_id: Mapped[int | None] = mapped_column(ForeignKey("alert_events.id"))
    occurrence_count_at_open: Mapped[int] = mapped_column(Integer, default=1)


class RecommendationLog(Base):
    __tablename__ = "recommendations_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint_id: Mapped[int] = mapped_column(ForeignKey("alert_fingerprints.id"))
    alert_event_id: Mapped[int] = mapped_column(ForeignKey("alert_events.id"))
    classification: Mapped[str] = mapped_column(String, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    notified_channel: Mapped[str | None] = mapped_column(String)
    notified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    human_reaction: Mapped[str | None] = mapped_column(String)
    # Added for the dashboard's incident feed -- previously the LLM's
    # natural-language explanation (app/llm/explain.py) was composed into
    # the Slack message and then discarded; it was never persisted
    # anywhere, so there was nothing in the DB for a dashboard to show.
    llm_explanation: Mapped[str | None] = mapped_column(Text)


class AlertDailyRollup(Base):
    """
    Per-day aggregate of alert_events written by the retention job
    (app/retention/prune.py) just before old events are deleted, so
    dashboard trend charts keep working for days whose raw events are
    gone. Dimension columns are NOT NULL with "" standing in for "unknown"
    -- a NULL would make the unique constraint useless (NULL != NULL in
    SQL), letting duplicate rows for the same bucket accumulate. Read
    paths convert "" back to None.

    priority is the EFFECTIVE priority (explicit alert priority, else the
    severity-derived fallback) at rollup time, matching what the
    dashboard shows for live events.
    """
    __tablename__ = "alert_daily_rollups"
    __table_args__ = (
        UniqueConstraint(
            "day", "vertical", "source_channel", "priority", "severity",
            name="uq_alert_daily_rollup_bucket",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    vertical: Mapped[str] = mapped_column(String, nullable=False, default="")
    source_channel: Mapped[str] = mapped_column(String, nullable=False, default="")
    # Display label only (latest non-empty name seen); not part of the key.
    source_channel_name: Mapped[str | None] = mapped_column(String)
    priority: Mapped[str] = mapped_column(String, nullable=False, default="")
    severity: Mapped[str] = mapped_column(String, nullable=False, default="")
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
