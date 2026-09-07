from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AlertEvent


@dataclass
class AlertStats:
    fingerprint_id: int
    occurrences_1h: int
    occurrences_24h: int
    occurrences_7d: int
    occurrences_30d: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    avg_interval_seconds: float | None


def _count_since(session: Session, fingerprint_id: int, since: datetime) -> int:
    stmt = select(func.count()).select_from(AlertEvent).where(
        AlertEvent.fingerprint_id == fingerprint_id,
        AlertEvent.occurred_at >= since,
    )
    return session.execute(stmt).scalar_one()


def compute_stats(session: Session, fingerprint_id: int) -> AlertStats:
    now = datetime.now(timezone.utc)

    occurrences_1h = _count_since(session, fingerprint_id, now - timedelta(hours=1))
    occurrences_24h = _count_since(session, fingerprint_id, now - timedelta(hours=24))
    occurrences_7d = _count_since(session, fingerprint_id, now - timedelta(days=7))
    occurrences_30d = _count_since(session, fingerprint_id, now - timedelta(days=30))

    timestamps = session.execute(
        select(AlertEvent.occurred_at)
        .where(AlertEvent.fingerprint_id == fingerprint_id)
        .order_by(AlertEvent.occurred_at)
    ).scalars().all()

    first_seen = timestamps[0] if timestamps else None
    last_seen = timestamps[-1] if timestamps else None

    avg_interval = None
    if len(timestamps) >= 2:
        deltas = [
            (timestamps[i] - timestamps[i - 1]).total_seconds()
            for i in range(1, len(timestamps))
        ]
        avg_interval = sum(deltas) / len(deltas)

    return AlertStats(
        fingerprint_id=fingerprint_id,
        occurrences_1h=occurrences_1h,
        occurrences_24h=occurrences_24h,
        occurrences_7d=occurrences_7d,
        occurrences_30d=occurrences_30d,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        avg_interval_seconds=avg_interval,
    )
