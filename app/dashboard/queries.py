from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.slack.channel_names import display_channel_name
from app.db.models import AlertDailyRollup, AlertEvent, AlertFingerprint, Incident, RecommendationLog

# Derived mapping, used ONLY as a fallback for alerts that don't carry
# an explicit priority from the source system (AlertEvent.priority is
# NULL). Real alerting tools often DO send an explicit priority already
# (e.g. Alertmanager's "priority: P0" label) -- when present, that value
# is always used as-is and this mapping is never consulted. This mapping
# does not change triage/routing/Jira logic anywhere else in the app;
# it's a dashboard display convention only.
SEVERITY_TO_PRIORITY = {
    "CRITICAL": "P0",
    "WARNING": "P1",
    "WARN": "P1",
    "INFO": "P2",
}
DEFAULT_PRIORITY = "P3"  # unparsed/unknown severity, no explicit priority either


def severity_to_priority(severity: str | None) -> str:
    return SEVERITY_TO_PRIORITY.get((severity or "").upper(), DEFAULT_PRIORITY)


def effective_priority(priority: str | None, severity: str | None) -> str:
    """The real, source-provided priority when the alert had one; the
    severity-derived fallback otherwise. Use this everywhere a per-alert
    priority is needed rather than calling severity_to_priority() directly."""
    return priority.upper() if priority else severity_to_priority(severity)


def _bucket_expr(session: Session, granularity: str):
    """
    granularity: "hour" or "day". Dialect-aware (SQLite strftime vs
    Postgres date_trunc), same pattern as the existing frequency-trend
    query -- centralized here so every time-series query shares it.
    """
    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind else "sqlite"
    is_sqlite = dialect_name == "sqlite"

    if granularity == "hour":
        return (
            func.strftime("%Y-%m-%d %H:00", AlertEvent.occurred_at)
            if is_sqlite
            else func.date_trunc("hour", AlertEvent.occurred_at)
        )
    return (
        func.strftime("%Y-%m-%d", AlertEvent.occurred_at)
        if is_sqlite
        else func.date_trunc("day", AlertEvent.occurred_at)
    )


def _format_bucket(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


# ---------------------------------------------------------------------
# Retention rollups. Raw alert_events older than
# settings.alert_event_retention_days are folded into alert_daily_rollups
# and deleted (app/retention/prune.py); events still referenced by
# recommendations_log/incidents are kept and never rolled up. So
# "live events + rollups" counts every alert exactly once, and the daily
# trend queries below add rollup counts for any window that reaches past
# the retention cutoff. Rollups are day-granular: a window starting
# mid-day includes that whole rolled-up day (same as a day-bucket chart).
# ---------------------------------------------------------------------

def _rollup_day_key(session: Session, day) -> str:
    """Format a rollup date exactly like _bucket_expr(..., "day") output
    is formatted for the active dialect, so both sources merge into the
    same bucket key."""
    bind = session.get_bind()
    if bind is None or bind.dialect.name == "sqlite":
        return day.isoformat()
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).isoformat()


def _since_date(since: datetime):
    if since.tzinfo is not None:
        since = since.astimezone(timezone.utc)
    return since.date()


def distinct_verticals(session: Session) -> list[str]:
    rows = session.execute(
        select(AlertEvent.vertical).where(AlertEvent.vertical.is_not(None)).distinct()
    ).all()
    return sorted(r[0] for r in rows)


def total_alerts(session: Session, since: datetime) -> int:
    return session.execute(
        select(func.count()).select_from(AlertEvent).where(AlertEvent.occurred_at >= since)
    ).scalar_one()


def alerts_by_dimension(session: Session, since: datetime, column) -> list[tuple[str, int]]:
    rows = session.execute(
        select(column, func.count())
        .select_from(AlertEvent)
        .where(AlertEvent.occurred_at >= since, column.is_not(None))
        .group_by(column)
        .order_by(func.count().desc())
    ).all()
    return [(r[0], r[1]) for r in rows]


def top_recurring_alerts(session: Session, since: datetime, limit: int = 10) -> list[dict]:
    rows = session.execute(
        select(
            AlertFingerprint.id,
            AlertFingerprint.alert_name,
            AlertFingerprint.service,
            AlertFingerprint.vertical,
            AlertFingerprint.last_classification,
            func.count(AlertEvent.id).label("occurrence_count"),
            # MAX picks an arbitrary-but-consistent channel per fingerprint
            # -- safe because channel now determines vertical (see
            # infer_vertical_service_from_channel), so a fingerprint's
            # events should all share one channel in practice. Avoids a
            # second query/subquery per row for what's normally a
            # single consistent value anyway.
            func.max(AlertEvent.source_channel).label("channel_id"),
            func.max(AlertEvent.source_channel_name).label("channel_name"),
            # Prefer an explicit priority on any event for this fingerprint;
            # fall back via effective_priority() using severity when absent.
            func.max(AlertEvent.priority).label("priority"),
            func.max(AlertEvent.severity).label("severity"),
        )
        .join(AlertEvent, AlertEvent.fingerprint_id == AlertFingerprint.id)
        .where(AlertEvent.occurred_at >= since)
        .group_by(AlertFingerprint.id)
        .order_by(func.count(AlertEvent.id).desc())
        .limit(limit)
    ).all()
    return [
        {
            "fingerprint_id": r[0],
            "alert_name": r[1],
            "service": r[2],
            "vertical": r[3],
            "classification": r[4],
            "occurrence_count": r[5],
            "channel_id": r[6],
            "channel_name": display_channel_name(r[6], r[7]),
            "priority": effective_priority(r[8], r[9]),
        }
        for r in rows
    ]


def alerts_by_classification(session: Session, classification: str, limit: int = 20) -> list[dict]:
    rows = session.execute(
        select(AlertFingerprint)
        .where(AlertFingerprint.last_classification == classification)
        .order_by(AlertFingerprint.last_seen_at.desc())
        .limit(limit)
    ).scalars().all()
    return [
        {
            "fingerprint_id": r.id,
            "alert_name": r.alert_name,
            "service": r.service,
            "vertical": r.vertical,
            "last_seen_at": r.last_seen_at,
        }
        for r in rows
    ]


def daily_frequency_trend(session: Session, days: int = 7) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    day_bucket = _bucket_expr(session, "day")

    rows = session.execute(
        select(day_bucket.label("day"), func.count())
        .where(AlertEvent.occurred_at >= since)
        .group_by(day_bucket)
        .order_by(day_bucket)
    ).all()

    totals: dict[str, int] = {}
    for bucket, count in rows:
        key = _format_bucket(bucket)
        totals[key] = totals.get(key, 0) + count

    rollups = session.execute(
        select(AlertDailyRollup.day, func.sum(AlertDailyRollup.count))
        .where(AlertDailyRollup.day >= _since_date(since))
        .group_by(AlertDailyRollup.day)
    ).all()
    for day, count in rollups:
        key = _rollup_day_key(session, day)
        totals[key] = totals.get(key, 0) + int(count or 0)

    return [{"day": k, "count": totals[k]} for k in sorted(totals)]


def notification_channel_split(session: Session, since: datetime) -> list[tuple[str, int]]:
    """
    Proxy for "Dev vs DevOps ownership": counts notifications sent per
    channel. True Dev-vs-DevOps categorization would require tagging each
    vertical_config channel with a role; for now this shows raw channel
    volume, which is still useful for spotting which channel is getting
    the most noise. Channel IDs are resolved to Slack names when possible.
    """
    rows = session.execute(
        select(RecommendationLog.notified_channel, func.count())
        .where(RecommendationLog.notified_at >= since, RecommendationLog.notified_channel.is_not(None))
        .group_by(RecommendationLog.notified_channel)
        .order_by(func.count().desc())
    ).all()
    return [(display_channel_name(r[0], None), r[1]) for r in rows]


# ---------------------------------------------------------------------
# Vertical-wise trends (added for the vertical-trends dashboard upgrade)
# ---------------------------------------------------------------------

def alert_volume_by_vertical_over_time(
    session: Session, since: datetime, granularity: str = "day"
) -> dict[str, list[dict]]:
    """
    Returns {vertical: [{bucket: "2026-08-13", count: N}, ...]}, one
    series per vertical, suitable for a Chart.js multi-line trend chart.
    Verticals with zero alerts in the window are omitted (nothing to
    plot) rather than returned as an empty series.
    """
    bucket_expr = _bucket_expr(session, granularity)

    rows = session.execute(
        select(AlertEvent.vertical, bucket_expr.label("bucket"), func.count())
        .where(AlertEvent.occurred_at >= since, AlertEvent.vertical.is_not(None))
        .group_by(AlertEvent.vertical, bucket_expr)
        .order_by(AlertEvent.vertical, bucket_expr)
    ).all()

    per_vertical: dict[str, dict[str, int]] = {}
    for vertical, bucket, count in rows:
        buckets = per_vertical.setdefault(vertical, {})
        key = _format_bucket(bucket)
        buckets[key] = buckets.get(key, 0) + count

    # Rollups are daily only; hourly views (24h range) stay live-only.
    if granularity == "day":
        rollups = session.execute(
            select(AlertDailyRollup.vertical, AlertDailyRollup.day, func.sum(AlertDailyRollup.count))
            .where(AlertDailyRollup.day >= _since_date(since), AlertDailyRollup.vertical != "")
            .group_by(AlertDailyRollup.vertical, AlertDailyRollup.day)
        ).all()
        for vertical, day, count in rollups:
            buckets = per_vertical.setdefault(vertical, {})
            key = _rollup_day_key(session, day)
            buckets[key] = buckets.get(key, 0) + int(count or 0)

    return {
        vertical: [{"bucket": k, "count": buckets[k]} for k in sorted(buckets)]
        for vertical, buckets in sorted(per_vertical.items())
    }


def priority_distribution_by_vertical(session: Session, since: datetime) -> dict[str, dict[str, int]]:
    """
    Returns {vertical: {"P0": n, "P1": n, "P2": n, "P3": n}}. Uses the
    alert's own explicit priority (AlertEvent.priority) when the source
    system provided one; falls back to severity_to_priority() only for
    alerts that didn't state a priority. See effective_priority().
    """
    rows = session.execute(
        select(AlertEvent.vertical, AlertEvent.severity, AlertEvent.priority, func.count())
        .where(AlertEvent.occurred_at >= since, AlertEvent.vertical.is_not(None))
        .group_by(AlertEvent.vertical, AlertEvent.severity, AlertEvent.priority)
    ).all()

    result: dict[str, dict[str, int]] = {}
    for vertical, severity, priority, count in rows:
        bucket = result.setdefault(vertical, {"P0": 0, "P1": 0, "P2": 0, "P3": 0})
        p = effective_priority(priority, severity)
        bucket[p] = bucket.get(p, 0) + count

    # Rollup priority is already the effective priority.
    rollups = session.execute(
        select(AlertDailyRollup.vertical, AlertDailyRollup.priority, func.sum(AlertDailyRollup.count))
        .where(AlertDailyRollup.day >= _since_date(since), AlertDailyRollup.vertical != "")
        .group_by(AlertDailyRollup.vertical, AlertDailyRollup.priority)
    ).all()
    for vertical, priority, count in rollups:
        bucket = result.setdefault(vertical, {"P0": 0, "P1": 0, "P2": 0, "P3": 0})
        p = priority or DEFAULT_PRIORITY
        bucket[p] = bucket.get(p, 0) + int(count or 0)
    return result


def avg_processing_seconds_by_vertical(session: Session, since: datetime) -> dict[str, float]:
    """
    "Processing time" here means alert-ingested -> notification-sent
    latency (AlertEvent.occurred_at -> RecommendationLog.notified_at for
    the same alert_event_id) -- the closest thing the schema tracks to
    "triage resolution/processing time" per alert. This is NOT the same
    as incident resolution time (Incident.opened_at -> closed_at, a
    human action) -- both are legitimate readings of the request; this
    one covers automated triage latency, which is what the bot actually
    controls. Only counts alerts that were actually notified (skipped/
    suppressed alerts have no RecommendationLog row and are excluded).
    """
    rows = session.execute(
        select(
            AlertEvent.vertical,
            AlertEvent.occurred_at,
            RecommendationLog.notified_at,
        )
        .join(RecommendationLog, RecommendationLog.alert_event_id == AlertEvent.id)
        .where(AlertEvent.occurred_at >= since, AlertEvent.vertical.is_not(None))
    ).all()

    totals: dict[str, list[float]] = {}
    for vertical, occurred_at, notified_at in rows:
        if occurred_at is None or notified_at is None:
            continue
        oa = occurred_at.replace(tzinfo=None) if occurred_at.tzinfo else occurred_at
        na = notified_at.replace(tzinfo=None) if notified_at.tzinfo else notified_at
        totals.setdefault(vertical, []).append((na - oa).total_seconds())

    return {v: sum(secs) / len(secs) for v, secs in totals.items() if secs}


def summary_cards(
    session: Session,
    since: datetime,
    vertical: str | None = None,
    severity: str | None = None,
) -> dict:
    """
    total_ingested: every alert event processed (all go through triage
      automatically, so this also IS the "auto-triaged" count -- see note
      below).
    jira_tickets_created: Incidents opened in the window.
    cooldown_suppressed: alert events in the window that reached the
      pipeline and were persisted, but did NOT result in a notification
      (blocked by the anti-noise cooldown in should_notify()) -- a
      cooldown-blocked alert has an AlertEvent row but no
      RecommendationLog row, which is the only place this distinction is
      recorded.
    deduplicated: Slack event deliveries dropped as duplicates BEFORE
      ever reaching the pipeline (see app/ingestion/dedup_guard.py) --
      these never become an AlertEvent row at all, so they are NOT
      queryable from the database. This number comes from an in-memory
      Prometheus counter instead, and is process-wide since startup
      (not scoped to the `since` window, and resets on restart) --
      genuinely different in kind from every other number on this card,
      not a rounding/approximation difference.
    suppressed_total: cooldown_suppressed + deduplicated, for a single
      "how much noise did we avoid sending" headline figure.
    """
    from prometheus_client import REGISTRY

    base_filters = [AlertEvent.occurred_at >= since]
    if vertical:
        base_filters.append(AlertEvent.vertical == vertical)
    if severity:
        base_filters.append(AlertEvent.severity == severity)

    total_ingested = session.execute(
        select(func.count()).select_from(AlertEvent).where(*base_filters)
    ).scalar_one()

    notified_query = (
        select(func.count())
        .select_from(AlertEvent)
        .join(RecommendationLog, RecommendationLog.alert_event_id == AlertEvent.id)
        .where(*base_filters)
    )
    notified_count = session.execute(notified_query).scalar_one()

    jira_filters = [Incident.opened_at >= since]
    if vertical:
        jira_filters.append(
            Incident.fingerprint_id.in_(
                select(AlertFingerprint.id).where(AlertFingerprint.vertical == vertical)
            )
        )
    jira_tickets_created = session.execute(
        select(func.count()).select_from(Incident).where(*jira_filters)
    ).scalar_one()

    cooldown_suppressed = max(total_ingested - notified_count, 0)
    deduplicated = int(
        REGISTRY.get_sample_value("alert_triage_alerts_deduplicated_total") or 0
    )

    return {
        "total_ingested": total_ingested,
        "auto_triaged": total_ingested,  # every ingested alert goes through triage automatically
        "notified": notified_count,
        "jira_tickets_created": jira_tickets_created,
        "cooldown_suppressed": cooldown_suppressed,
        "deduplicated": deduplicated,
        "suppressed_total": cooldown_suppressed + deduplicated,
        # Kept for backward compatibility with anything still reading the
        # old key name.
        "suppressed": cooldown_suppressed,
    }


def incident_feed(
    session: Session,
    since: datetime,
    vertical: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Recent incidents (Jira-tracked, i.e. ticket-worthy alerts only -- see
    should_open_incident) joined with the fingerprint they belong to and
    the most recent RecommendationLog entry for that fingerprint (for the
    rationale + LLM explanation text). Correlated-alert counts are NOT
    stored anywhere (find_related_alerts is computed live at notify time
    and never persisted), so this recomputes them on the fly for each row
    -- fine at dashboard-page volumes, would need denormalizing if this
    endpoint ever needs to handle high query volume.
    """
    from app.correlation.correlate import find_related_alerts

    filters = [Incident.opened_at >= since]
    if status:
        filters.append(Incident.status == status)

    query = (
        select(Incident, AlertFingerprint)
        .join(AlertFingerprint, Incident.fingerprint_id == AlertFingerprint.id)
        .where(*filters)
        .order_by(Incident.opened_at.desc())
        .limit(limit)
    )
    if vertical:
        query = query.where(AlertFingerprint.vertical == vertical)

    rows = session.execute(query).all()

    results = []
    now = datetime.now(timezone.utc)
    for incident, fp in rows:
        # Fetch the latest event's severity, priority, and channel info in
        # one query -- also fixes a bug where priority was only ever
        # computed when the `severity` filter param was active, silently
        # defaulting every other incident to P3 regardless of its real
        # severity.
        latest_event = session.execute(
            select(
                AlertEvent.severity,
                AlertEvent.priority,
                AlertEvent.source_channel,
                AlertEvent.source_channel_name,
            )
            .where(AlertEvent.fingerprint_id == fp.id)
            .order_by(AlertEvent.occurred_at.desc())
            .limit(1)
        ).first()

        latest_severity = latest_event[0] if latest_event else None
        latest_priority = latest_event[1] if latest_event else None
        channel_id = latest_event[2] if latest_event else None
        channel_name = latest_event[3] if latest_event else None

        if severity and latest_severity != severity:
            continue

        latest_rec = session.execute(
            select(RecommendationLog)
            .where(RecommendationLog.fingerprint_id == fp.id)
            .order_by(RecommendationLog.notified_at.desc())
            .limit(1)
        ).scalars().first()

        related = find_related_alerts(
            session=session,
            vertical=fp.vertical,
            service=fp.service,
            exclude_fingerprint_id=fp.id,
            now=now,
        )

        results.append(
            {
                "incident_id": incident.id,
                "fingerprint_id": fp.id,
                "alert_name": fp.alert_name,
                "service": fp.service,
                "vertical": fp.vertical,
                "channel_id": channel_id,
                "channel_name": display_channel_name(channel_id, channel_name),
                "classification": fp.last_classification,
                "priority": effective_priority(latest_priority, latest_severity),
                "status": incident.status,
                "jira_issue_key": incident.jira_issue_key,
                "jira_url": incident.jira_url,
                "opened_at": incident.opened_at,
                "closed_at": incident.closed_at,
                "correlated_alert_count": len(related),
                "rationale": latest_rec.rationale if latest_rec else None,
                "llm_explanation": latest_rec.llm_explanation if latest_rec else None,
            }
        )
    return results
