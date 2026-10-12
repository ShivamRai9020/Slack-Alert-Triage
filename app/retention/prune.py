"""
Retention for alert_events.

alert_events grows by one row per firing forever unless something prunes
it. run_retention():

  1. picks events with occurred_at < now - alert_event_retention_days,
  2. adds their counts into alert_daily_rollups (per UTC day / vertical /
     source_channel / effective priority / severity), and
  3. deletes those events -- in batches, rollup-add + delete committed in
     ONE transaction per batch, so a crash mid-run can never count an
     event twice or lose it from the rollup.

FK safety: two tables reference alert_events.id --
  * recommendations_log.alert_event_id (NOT NULL in the model; one row per
    notification sent), and
  * incidents.last_linked_alert_event_id (nullable; the event that last
    touched a Jira incident).
Events referenced by EITHER are KEPT (never rolled up, never deleted).
Nulling recommendations_log.alert_event_id isn't allowed by the model and
would break the dashboard's "notified" count and processing-time metric
(both join recommendations_log -> alert_events); nulling the incident link
would lose the "which alert caused this ticket" pointer. Notified events
are a small, throttled fraction of all firings (P0 debounce / spike
threshold), so keeping them costs little. Because kept events are never
rolled up, they are counted live by the dashboard exactly once.
alert_fingerprints / incidents / recommendations_log rows are never
modified or deleted here.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select, union
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AlertDailyRollup, AlertEvent, Incident, RecommendationLog

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1000
_MAX_BATCH_RETRIES = 3

class _BatchConflict(Exception):
    """Part of a batch was deleted by a concurrent retention run."""


RollupKey = tuple[date, str, str, str, str]  # day, vertical, channel, priority, severity


@dataclass
class RetentionResult:
    enabled: bool
    dry_run: bool = False
    retention_days: int = 0
    cutoff: datetime | None = None
    rolled_up: int = 0           # events added to rollups (== deleted unless dry run)
    deleted: int = 0
    kept_referenced: int = 0     # old events kept because FK-referenced
    batches: int = 0
    buckets: dict[RollupKey, int] = field(default_factory=dict)

    def summary(self) -> str:
        if not self.enabled:
            return "retention disabled (alert_event_retention_days <= 0)"
        verb = "would roll up/delete" if self.dry_run else "rolled up/deleted"
        return (
            f"cutoff={self.cutoff.isoformat() if self.cutoff else None} "
            f"{verb} {self.rolled_up} events into {len(self.buckets)} day-buckets; "
            f"kept {self.kept_referenced} old events referenced by "
            f"recommendations_log/incidents"
        )


def _as_utc_date(value: datetime) -> date:
    # SQLite returns naive datetimes; everything is written as UTC (utcnow()).
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(timezone.utc).date()


def _effective_priority(priority: str | None, severity: str | None) -> str:
    # Imported lazily: dashboard.queries imports the rollup model too.
    from app.dashboard.queries import effective_priority

    return effective_priority(priority, severity)


def _referenced_event_ids():
    return union(
        select(RecommendationLog.alert_event_id).where(RecommendationLog.alert_event_id.is_not(None)),
        select(Incident.last_linked_alert_event_id).where(
            Incident.last_linked_alert_event_id.is_not(None)
        ),
    )


def _upsert_buckets(
    session: Session, counts: dict[RollupKey, int], names: dict[RollupKey, str]
) -> None:
    now = datetime.now(timezone.utc)
    for key, n in counts.items():
        day, vertical, channel, priority, severity = key
        row = session.execute(
            select(AlertDailyRollup).where(
                AlertDailyRollup.day == day,
                AlertDailyRollup.vertical == vertical,
                AlertDailyRollup.source_channel == channel,
                AlertDailyRollup.priority == priority,
                AlertDailyRollup.severity == severity,
            )
        ).scalars().first()
        if row is None:
            session.add(
                AlertDailyRollup(
                    day=day,
                    vertical=vertical,
                    source_channel=channel,
                    source_channel_name=names.get(key),
                    priority=priority,
                    severity=severity,
                    count=n,
                    updated_at=now,
                )
            )
        else:
            row.count = (row.count or 0) + n
            if names.get(key):
                row.source_channel_name = names[key]
            row.updated_at = now
    session.flush()


def run_retention(
    session: Session,
    now: datetime | None = None,
    *,
    retention_days: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> RetentionResult:
    days = settings.alert_event_retention_days if retention_days is None else retention_days
    if days is None or days <= 0:
        return RetentionResult(enabled=False, dry_run=dry_run)

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=days)
    result = RetentionResult(enabled=True, dry_run=dry_run, retention_days=days, cutoff=cutoff)

    referenced = _referenced_event_ids()
    result.kept_referenced = session.execute(
        select(func.count())
        .select_from(AlertEvent)
        .where(AlertEvent.occurred_at < cutoff, AlertEvent.id.in_(referenced))
    ).scalar_one()

    last_id = 0
    retries = 0
    while True:
        rows = session.execute(
            select(
                AlertEvent.id,
                AlertEvent.occurred_at,
                AlertEvent.vertical,
                AlertEvent.source_channel,
                AlertEvent.source_channel_name,
                AlertEvent.priority,
                AlertEvent.severity,
            )
            .where(
                AlertEvent.occurred_at < cutoff,
                AlertEvent.id > last_id,
                AlertEvent.id.not_in(_referenced_event_ids()),
            )
            .order_by(AlertEvent.id)
            .limit(batch_size)
        ).all()
        if not rows:
            break

        counts: dict[RollupKey, int] = defaultdict(int)
        names: dict[RollupKey, str] = {}
        ids = []
        for ev_id, occurred_at, vertical, channel, channel_name, priority, severity in rows:
            key = (
                _as_utc_date(occurred_at),
                vertical or "",
                channel or "",
                _effective_priority(priority, severity),
                severity or "",
            )
            counts[key] += 1
            if channel_name:
                names[key] = channel_name
            ids.append(ev_id)

        if dry_run:
            last_id = ids[-1]
        else:
            try:
                deleted = session.execute(
                    delete(AlertEvent).where(AlertEvent.id.in_(ids)),
                    execution_options={"synchronize_session": False},
                ).rowcount
                if deleted != len(ids):
                    # Someone else (another worker's retention run) deleted
                    # part of this batch first -- don't roll up rows we
                    # didn't delete. Re-read and retry.
                    raise _BatchConflict()
                _upsert_buckets(session, counts, names)
                session.commit()
            except (IntegrityError, _BatchConflict):
                session.rollback()
                retries += 1
                if retries > _MAX_BATCH_RETRIES:
                    logger.warning("Retention: giving up after %s batch retries", retries - 1)
                    break
                continue
            retries = 0
            result.deleted += len(ids)
            # Deleted rows are gone; still advance so we never re-scan.
            last_id = ids[-1]

        result.batches += 1
        result.rolled_up += len(ids)
        for key, n in counts.items():
            result.buckets[key] = result.buckets.get(key, 0) + n

    if dry_run:
        session.rollback()
    logger.info("Retention run: %s", result.summary())
    return result
