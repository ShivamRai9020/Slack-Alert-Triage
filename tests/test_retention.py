from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.dashboard import queries
from app.db.models import (
    AlertDailyRollup,
    AlertEvent,
    AlertFingerprint,
    Base,
    Incident,
    RecommendationLog,
)
from app.retention import scheduler
from app.retention.prune import run_retention

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    # Enforce FKs so deleting a referenced alert_event would raise.
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _fp(session, h="h1", vertical="digitalcst"):
    fp = AlertFingerprint(fingerprint_hash=h, alert_name="A", service="svc", vertical=vertical)
    session.add(fp)
    session.flush()
    return fp


def _ev(session, fp, days_ago, *, hours=0, priority="P1", severity="critical",
        vertical="digitalcst", channel="C1", channel_name="cst-alerts"):
    e = AlertEvent(
        fingerprint_id=fp.id,
        source_channel=channel,
        source_channel_name=channel_name,
        raw_message="x",
        vertical=vertical,
        priority=priority,
        severity=severity,
        occurred_at=(NOW - timedelta(days=days_ago, hours=hours)).replace(tzinfo=None),
    )
    session.add(e)
    session.flush()
    return e


def _count(session, model):
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def _rollups(session):
    return {
        (r.day.isoformat(), r.vertical, r.source_channel, r.priority, r.severity): r.count
        for r in session.execute(select(AlertDailyRollup)).scalars()
    }


def test_rollup_counts_per_day_and_dimensions(session):
    fp = _fp(session)
    for _ in range(3):
        _ev(session, fp, 40)                       # 2026-09-02 P1
    _ev(session, fp, 40, priority=None, severity="warning")  # effective P1 via severity
    _ev(session, fp, 40, priority=None, severity=None)       # effective P3
    _ev(session, fp, 35, vertical=None, channel="C2", channel_name=None)
    session.commit()

    res = run_retention(session, now=NOW, retention_days=30)

    assert res.rolled_up == 6 and res.deleted == 6
    assert _rollups(session) == {
        ("2026-09-02", "digitalcst", "C1", "P1", "critical"): 3,
        ("2026-09-02", "digitalcst", "C1", "P1", "warning"): 1,
        ("2026-09-02", "digitalcst", "C1", "P3", ""): 1,
        ("2026-09-07", "", "C2", "P1", "critical"): 1,
    }
    row = session.execute(select(AlertDailyRollup).where(AlertDailyRollup.count == 3)).scalar_one()
    assert row.source_channel_name == "cst-alerts"


def test_deletes_only_events_older_than_cutoff(session):
    fp = _fp(session)
    old = _ev(session, fp, 31)
    keep_recent = _ev(session, fp, 29)
    keep_today = _ev(session, fp, 0)
    session.commit()
    old_id, recent_ids = old.id, {keep_recent.id, keep_today.id}

    run_retention(session, now=NOW, retention_days=30, batch_size=1)

    remaining = set(session.execute(select(AlertEvent.id)).scalars())
    assert remaining == recent_ids
    assert old_id not in remaining


def test_batches_and_idempotent(session):
    fp = _fp(session)
    for _ in range(7):
        _ev(session, fp, 45)
    session.commit()

    first = run_retention(session, now=NOW, retention_days=30, batch_size=3)
    assert first.batches == 3 and first.deleted == 7
    second = run_retention(session, now=NOW, retention_days=30, batch_size=3)
    assert second.rolled_up == 0 and second.deleted == 0
    assert _rollups(session) == {("2026-08-28", "digitalcst", "C1", "P1", "critical"): 7}

    # A later run that adds to an existing bucket increments, not duplicates.
    _ev(session, fp, 45)
    session.commit()
    run_retention(session, now=NOW, retention_days=30)
    assert _rollups(session) == {("2026-08-28", "digitalcst", "C1", "P1", "critical"): 8}
    assert _count(session, AlertDailyRollup) == 1


def test_fk_referenced_events_are_kept_and_not_rolled_up(session):
    fp = _fp(session)
    notified = _ev(session, fp, 50)
    ticketed = _ev(session, fp, 50)
    plain = _ev(session, fp, 50)
    session.add(RecommendationLog(fingerprint_id=fp.id, alert_event_id=notified.id,
                                  classification="requires_investigation"))
    session.add(Incident(fingerprint_id=fp.id, last_linked_alert_event_id=ticketed.id))
    session.commit()
    kept = {notified.id, ticketed.id}
    plain_id = plain.id

    res = run_retention(session, now=NOW, retention_days=30)

    assert res.kept_referenced == 2
    assert res.deleted == 1
    assert set(session.execute(select(AlertEvent.id)).scalars()) == kept
    assert plain_id not in kept
    assert _count(session, RecommendationLog) == 1
    assert _count(session, Incident) == 1
    assert _count(session, AlertFingerprint) == 1
    assert sum(_rollups(session).values()) == 1  # kept events counted live, not rolled up
    # FK enforcement is on in this fixture; no dangling references remain.
    assert session.execute(
        select(func.count()).select_from(RecommendationLog)
        .where(RecommendationLog.alert_event_id.not_in(select(AlertEvent.id)))
    ).scalar_one() == 0


def test_disabled_when_zero(session, monkeypatch):
    fp = _fp(session)
    _ev(session, fp, 400)
    session.commit()

    res = run_retention(session, now=NOW, retention_days=0)
    assert res.enabled is False
    assert _count(session, AlertEvent) == 1
    assert _count(session, AlertDailyRollup) == 0

    from app.config import settings
    monkeypatch.setattr(settings, "alert_event_retention_days", 0)
    assert run_retention(session, now=NOW).enabled is False
    assert scheduler.start(startup_delay=3600) is None


def test_dry_run_changes_nothing(session):
    fp = _fp(session)
    _ev(session, fp, 40)
    _ev(session, fp, 40)
    session.commit()

    res = run_retention(session, now=NOW, retention_days=30, dry_run=True, batch_size=1)
    assert res.rolled_up == 2 and res.deleted == 0
    assert sum(res.buckets.values()) == 2
    assert _count(session, AlertEvent) == 2
    assert _count(session, AlertDailyRollup) == 0


def test_dashboard_trends_combine_live_and_rollups(session):
    """Retention shorter than the view window: totals unchanged after pruning."""
    fp = _fp(session)
    real_now = datetime.now(timezone.utc)
    for days_ago, prio in [(20, "P0"), (20, "P1"), (12, "P2"), (2, "P1"), (1, None)]:
        session.add(AlertEvent(
            fingerprint_id=fp.id, source_channel="C1", raw_message="x",
            vertical="digitalcst", priority=prio, severity="info",
            occurred_at=(real_now - timedelta(days=days_ago)).replace(tzinfo=None),
        ))
    session.commit()
    since = real_now - timedelta(days=30)

    before = (
        queries.daily_frequency_trend(session, 30),
        queries.alert_volume_by_vertical_over_time(session, since, "day"),
        queries.priority_distribution_by_vertical(session, since),
    )
    res = run_retention(session, now=real_now, retention_days=7)
    assert res.deleted == 3
    after = (
        queries.daily_frequency_trend(session, 30),
        queries.alert_volume_by_vertical_over_time(session, since, "day"),
        queries.priority_distribution_by_vertical(session, since),
    )

    assert after == before
    assert after[2]["digitalcst"] == {"P0": 1, "P1": 2, "P2": 2, "P3": 0}
    assert sum(d["count"] for d in after[0]) == 5

    # A 7-day window does not pull in rollups from older days.
    assert sum(d["count"] for d in queries.daily_frequency_trend(session, 7)) == 2
