"""
First-pass correlation: when a high-severity alert fires, surface other
DISTINCT alert types (different fingerprints) that also fired in the same
vertical/service within a short window. This is intentionally simple --
co-occurrence in the same vertical/service/time window, not a causal
claim -- and is meant as a "you might want to look at these together"
hint for the on-call, not an automatic root-cause determination.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AlertEvent, AlertFingerprint


@dataclass
class RelatedAlert:
    alert_name: str | None
    fingerprint_id: int
    occurred_at: datetime


def find_related_alerts(
    session: Session,
    vertical: str | None,
    service: str | None,
    exclude_fingerprint_id: int,
    now: datetime,
    window_minutes: int | None = None,
) -> list[RelatedAlert]:
    if not vertical:
        return []

    window = window_minutes or settings.correlation_window_minutes
    since = now - timedelta(minutes=window)

    query = (
        select(AlertEvent.occurred_at, AlertFingerprint.alert_name, AlertFingerprint.id)
        .join(AlertFingerprint, AlertEvent.fingerprint_id == AlertFingerprint.id)
        .where(
            AlertEvent.vertical == vertical,
            AlertEvent.occurred_at >= since,
            AlertFingerprint.id != exclude_fingerprint_id,
        )
    )
    if service:
        query = query.where(AlertEvent.service == service)

    query = query.order_by(AlertEvent.occurred_at.desc()).limit(50)

    rows = session.execute(query).all()

    # Dedupe to one (most recent) row per fingerprint in Python -- avoids
    # relying on Postgres DISTINCT ON, whose column-ordering requirements
    # would conflict with sorting by recency here.
    seen: dict[int, RelatedAlert] = {}
    for occurred_at, alert_name, fingerprint_id in rows:
        if fingerprint_id not in seen:
            seen[fingerprint_id] = RelatedAlert(
                alert_name=alert_name, fingerprint_id=fingerprint_id, occurred_at=occurred_at
            )
        if len(seen) >= 5:
            break

    return list(seen.values())
