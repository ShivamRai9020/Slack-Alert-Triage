from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.correlation.correlate import find_related_alerts


def _row(minutes_ago: int, alert_name: str, fingerprint_id: int, now: datetime):
    return (now - timedelta(minutes=minutes_ago), alert_name, fingerprint_id)


def test_dedupes_to_most_recent_per_fingerprint():
    now = datetime.now(timezone.utc)
    rows = [
        _row(1, "API 5xx", 2, now),
        _row(5, "API 5xx", 2, now),  # older duplicate of fingerprint 2 -- should be dropped
        _row(2, "DB Timeout", 3, now),
    ]

    session = MagicMock()
    session.execute.return_value.all.return_value = rows

    results = find_related_alerts(
        session=session,
        vertical="payments",
        service="checkout-api",
        exclude_fingerprint_id=1,
        now=now,
    )

    fingerprint_ids = [r.fingerprint_id for r in results]
    assert fingerprint_ids.count(2) == 1
    assert 3 in fingerprint_ids
    assert len(results) == 2


def test_returns_empty_list_when_no_vertical():
    session = MagicMock()
    results = find_related_alerts(
        session=session,
        vertical=None,
        service="checkout-api",
        exclude_fingerprint_id=1,
        now=datetime.now(timezone.utc),
    )
    assert results == []
    session.execute.assert_not_called()


def test_caps_at_five_related_alerts():
    now = datetime.now(timezone.utc)
    rows = [_row(1, f"Alert {i}", i, now) for i in range(2, 12)]  # 10 distinct fingerprints

    session = MagicMock()
    session.execute.return_value.all.return_value = rows

    results = find_related_alerts(
        session=session,
        vertical="payments",
        service=None,
        exclude_fingerprint_id=1,
        now=now,
    )
    assert len(results) == 5
