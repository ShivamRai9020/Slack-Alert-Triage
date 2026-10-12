import threading

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.dashboard.routes import router as dashboard_router
from app.db.models import AlertFingerprint, Incident, SourceChannel, TriageRule, VerticalConfig
from app.db.session import get_session, init_db
from app.observability.logging_config import configure_logging
from app.security.auth import require_api_key
from app.security.rate_limit import is_rate_limited
from app.slack.channel_names import display_channel_name
from app.stats.stats_engine import compute_stats
from app.triage.rules_repository import invalidate_cache, seed_default_rules_if_empty

configure_logging(settings.log_level)
import logging  # noqa: E402  -- after configure_logging so getLogger picks up the configured root

logger = logging.getLogger(__name__)

app = FastAPI(title="Intelligent Alert Triage Bot")
app.include_router(dashboard_router)


@app.on_event("startup")
def on_startup():
    init_db()
    logger.warning(
        "init_db() only fills in missing tables (checkfirst=True) -- it does not "
        "apply migrations to existing tables. Run `alembic upgrade head` as part "
        "of your deploy process for real schema changes."
    )

    if not settings.internal_api_key:
        logger.warning(
            "INTERNAL_API_KEY is not set -- /config, /incidents, and /internal/events "
            "are running WITHOUT authentication. Fine for local dev only; set "
            "INTERNAL_API_KEY before deploying anywhere reachable outside your machine."
        )

    from app.db.session import SessionLocal

    seed_session = SessionLocal()
    try:
        seed_default_rules_if_empty(seed_session)
    finally:
        seed_session.close()

    # Background data retention (rollup + prune old alert_events); no-op
    # when ALERT_EVENT_RETENTION_DAYS=0. See app/retention/.
    from app.retention import scheduler as retention_scheduler

    retention_scheduler.start()

    if settings.slack_bot_token and settings.slack_app_token:
        from app.ingestion.slack_listener import start_socket_mode

        thread = threading.Thread(
            target=start_socket_mode, args=(settings.slack_app_token,), daemon=True
        )
        thread.start()
        logger.info("Slack Socket Mode listener started in background thread.")
    else:
        logger.warning(
            "SLACK_BOT_TOKEN / SLACK_APP_TOKEN not set — Slack listener not started. "
            "Use POST /internal/events or the mock generator to feed alerts."
        )


@app.on_event("shutdown")
def on_shutdown():
    from app.retention import scheduler as retention_scheduler

    retention_scheduler.stop()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/alerts/{fingerprint_id}/stats")
def get_stats(fingerprint_id: int, session: Session = Depends(get_session)):
    return compute_stats(session, fingerprint_id)


@app.get("/alerts/{fingerprint_id}/history")
def get_history(fingerprint_id: int, session: Session = Depends(get_session)):
    fp = session.get(AlertFingerprint, fingerprint_id)
    if not fp:
        return {"error": "not found"}
    return {
        "fingerprint": fp.fingerprint_hash,
        "alert_name": fp.alert_name,
        "events": [
            {"occurred_at": e.occurred_at.isoformat(), "raw_message": e.raw_message}
            for e in fp.events
        ],
    }


@app.get("/config/verticals", dependencies=[Depends(require_api_key)])
def list_verticals(session: Session = Depends(get_session)):
    rows = session.execute(select(VerticalConfig)).scalars().all()
    return [
        {
            "vertical": r.vertical,
            "service": r.service,
            "alert_channel": r.alert_channel,
            "channel_name": r.channel_name,
            "dev_notify_channel": r.dev_notify_channel,
            "devops_notify_channel": r.devops_notify_channel,
        }
        for r in rows
    ]


@app.post("/config/verticals", dependencies=[Depends(require_api_key)])
def create_vertical(payload: dict, session: Session = Depends(get_session)):
    row = VerticalConfig(**payload)
    session.add(row)
    session.commit()
    return {"id": row.id}


@app.get("/config/channels", dependencies=[Depends(require_api_key)])
def list_channels(session: Session = Depends(get_session)):
    rows = session.execute(select(SourceChannel)).scalars().all()
    return [
        {
            "channel_id": r.channel_id,
            "channel_name": display_channel_name(r.channel_id, r.channel_name),
            "vertical": r.vertical,
            "service": r.service,
        }
        for r in rows
    ]


@app.post("/config/channels", dependencies=[Depends(require_api_key)])
def upsert_channel(payload: dict, session: Session = Depends(get_session)):
    """
    UPSERT on channel_id, unlike /config/verticals (insert-only, a
    repeated source of confusion during testing -- re-posting the same
    vertical/service/alert_name there creates a duplicate row rather than
    updating). channel_id is a real unique key here, so there's no
    ambiguity about which row "the same channel" means -- upserting is
    both safe and clearly the more useful behavior for something you'll
    likely correct/relabel as you onboard channels.
    """
    channel_id = payload["channel_id"]
    existing = session.execute(
        select(SourceChannel).where(SourceChannel.channel_id == channel_id)
    ).scalars().first()

    if existing:
        existing.channel_name = payload.get("channel_name", existing.channel_name)
        existing.vertical = payload.get("vertical", existing.vertical)
        existing.service = payload.get("service", existing.service)
        session.commit()
        return {"id": existing.id, "updated": True}

    row = SourceChannel(
        channel_id=channel_id,
        channel_name=payload.get("channel_name"),
        vertical=payload["vertical"],
        service=payload.get("service"),
    )
    session.add(row)
    session.commit()
    return {"id": row.id, "updated": False}


@app.get("/config/triage-rules", dependencies=[Depends(require_api_key)])
def list_triage_rules(session: Session = Depends(get_session)):
    rows = session.execute(select(TriageRule)).scalars().all()
    return [
        {
            "id": r.id,
            "rule_name": r.rule_name,
            "classification": r.classification,
            "condition": r.condition_json,
            "priority": r.priority,
            "active": r.active,
        }
        for r in rows
    ]


@app.post("/config/triage-rules", dependencies=[Depends(require_api_key)])
def create_triage_rule(payload: dict, session: Session = Depends(get_session)):
    """
    condition keys must be one of app.triage.triage_engine.SUPPORTED_CONDITIONS
    -- an unsupported key raises ValueError at classification time, not here,
    so double check against that list before adding a rule.
    """
    row = TriageRule(
        rule_name=payload["rule_name"],
        classification=payload["classification"],
        condition_json=payload["condition"],
        priority=payload.get("priority", 100),
        active=payload.get("active", True),
    )
    session.add(row)
    session.commit()
    invalidate_cache()
    return {"id": row.id}


@app.post("/internal/events")
def ingest_event(
    payload: dict,
    session: Session = Depends(get_session),
    x_api_key: str | None = Header(default=None),
):
    """
    Generic ingestion endpoint. Used by the mock alert generator today;
    this is also the seam where a future Alertmanager/PagerDuty webhook
    adapter would call in, keeping the pipeline source-agnostic.

    Rate-limited (per API key, or per "anonymous" if auth is disabled)
    rather than gated by require_api_key alone -- this is the one endpoint
    an external caller hitting repeatedly could use to flood the pipeline,
    run up Jira ticket creation, or run up LLM API costs.
    """
    require_api_key(x_api_key)

    client_key = x_api_key or "anonymous"
    if is_rate_limited(client_key):
        raise HTTPException(status_code=429, detail="Rate limit exceeded, slow down")

    from app.ingestion.pipeline import process_raw_message

    result = process_raw_message(
        session=session,
        channel=payload["channel"],
        raw_message=payload["raw_message"],
        slack_ts=payload.get("slack_ts"),
        slack_thread_ts=payload.get("slack_thread_ts"),
        channel_name_hint=payload.get("channel_name"),
    )
    return result


@app.get("/incidents", dependencies=[Depends(require_api_key)])
def list_incidents(status: str = "open", session: Session = Depends(get_session)):
    rows = session.execute(select(Incident).where(Incident.status == status)).scalars().all()
    return [
        {
            "id": r.id,
            "fingerprint_id": r.fingerprint_id,
            "jira_issue_key": r.jira_issue_key,
            "jira_url": r.jira_url,
            "status": r.status,
            "opened_at": r.opened_at.isoformat(),
            "closed_at": r.closed_at.isoformat() if r.closed_at else None,
        }
        for r in rows
    ]


@app.post("/incidents/{incident_id}/resolve", dependencies=[Depends(require_api_key)])
def resolve_incident(incident_id: int, session: Session = Depends(get_session)):
    """
    Manual resolution only -- ticket CREATION is automated (non-destructive),
    but marking an incident resolved is a human judgment call the bot
    should never make on its own, consistent with the human-in-the-loop
    constraint on the rest of this system.
    """
    from datetime import datetime, timezone

    incident = session.get(Incident, incident_id)
    if not incident:
        return {"error": "not found"}

    incident.status = "resolved"
    incident.closed_at = datetime.now(timezone.utc)
    session.commit()
    return {"id": incident.id, "status": incident.status}
