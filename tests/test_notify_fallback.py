"""When routing is empty, notifications fall back to the source channel."""

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.ingestion.pipeline import process_raw_message
from app.parsing.profiles import ALERTMANAGER_PATTERNS  # noqa: F401 — ensure importable


REAL_ALERT = """[FIRING:1] HostHighCpuLoad >75 10.210.9.231:9100  ||  techteam: pplusprod   || severity: critical   || priority: P0
Labels:
bu:  | techteam: pplusprod   | severity : critical 

instance: 10.210.9.231:9100

Annotations:
- description = Application = postprocessing_pg2 Environment = rtdd
"""


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_alertmanager_fields_stored_and_notify_falls_back_to_source(monkeypatch):
    session = _session()
    posted = {}

    def fake_dispatch(session, channel, message, fingerprint, alert_event_id, classification, rationale, llm_explanation=None):
        posted["channel"] = channel
        posted["message"] = message
        fingerprint.last_notified_at = datetime.now(timezone.utc)
        fingerprint.last_classification = classification
        session.commit()

    monkeypatch.setattr("app.ingestion.pipeline.dispatch", fake_dispatch)
    monkeypatch.setattr("app.ingestion.pipeline.should_notify", lambda *a, **k: True)
    # Keep Jira/LLM quiet
    monkeypatch.setattr("app.ingestion.pipeline.ensure_incident", lambda **kwargs: None)
    monkeypatch.setattr("app.ingestion.pipeline.explain", lambda *a, **k: None)
    monkeypatch.setattr("app.ingestion.pipeline.suggest_duplicate", lambda **k: None)

    result = process_raw_message(
        session=session,
        channel="C_SOURCE123",
        raw_message=REAL_ALERT,
        channel_name_hint="pg-aws",
    )

    assert result["notified"] is True
    assert result["notified_channel"] == "C_SOURCE123"
    assert result["notify_via"] == "source_channel_fallback"
    assert posted["channel"] == "C_SOURCE123"

    from app.db.models import AlertEvent
    event = session.query(AlertEvent).one()
    assert event.vertical == "pplusprod"
    assert event.priority == "P0"
    assert event.service == "postprocessing_pg2"
    assert event.source_channel_name == "pg-aws"
