from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import AlertEvent, AlertFingerprint, Base
from app.notification.dispatcher import effective_priority, should_notify


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _fp(session, **kwargs):
    fp = AlertFingerprint(
        fingerprint_hash=kwargs.pop("hash", "h1"),
        alert_name="HostHighCpuLoad",
        service="svc",
        vertical="pplusprod",
        **kwargs,
    )
    session.add(fp)
    session.flush()
    return fp


def _add_events(session, fp, n, *, minutes_ago_start=0):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(n):
        session.add(
            AlertEvent(
                fingerprint_id=fp.id,
                source_channel="C1",
                raw_message="x",
                occurred_at=now - timedelta(minutes=minutes_ago_start) - timedelta(seconds=i),
            )
        )
    session.commit()


def test_effective_priority_prefers_explicit():
    assert effective_priority("p0", "WARNING") == "P0"
    assert effective_priority(None, "CRITICAL") == "P0"
    assert effective_priority(None, "WARNING") == "P1"
    assert effective_priority(None, None) == "P3"


def test_p0_notifies_every_time_after_debounce(monkeypatch):
    monkeypatch.setattr("app.notification.dispatcher.settings.p0_debounce_minutes", 2)
    session = _session()
    fp = _fp(session)
    assert should_notify(session, fp, "P0") is True

    fp.last_notified_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=30)
    session.commit()
    assert should_notify(session, fp, "P0") is False

    fp.last_notified_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=3)
    session.commit()
    assert should_notify(session, fp, "P0") is True


def test_non_p0_requires_five_in_hour(monkeypatch):
    monkeypatch.setattr("app.notification.dispatcher.settings.spike_threshold", 5)
    monkeypatch.setattr("app.notification.dispatcher.settings.spike_window_minutes", 60)
    monkeypatch.setattr("app.notification.dispatcher.settings.notification_cooldown_minutes", 15)
    session = _session()
    fp = _fp(session, hash="h2")
    _add_events(session, fp, 4)
    assert should_notify(session, fp, "P2") is False

    _add_events(session, fp, 1)
    assert should_notify(session, fp, "P2") is True

    fp.last_notified_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.commit()
    assert should_notify(session, fp, "P2") is False

    fp.last_notified_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=16)
    session.commit()
    assert should_notify(session, fp, "P1", "WARNING") is True
