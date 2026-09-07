from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.dashboard import queries
from app.dashboard.actions import reevaluate_fingerprint, sync_jira_status
from app.db.models import AlertEvent, AlertFingerprint
from app.db.session import get_session
from app.jira.client import JiraClient
from app.security.auth import require_api_key

router = APIRouter()
templates = Jinja2Templates(directory="app/dashboard/templates")

CLASSIFICATION_CSS = {
    "potential_noise": "noise",
    "high_frequency_spike": "spike",
    "new_unknown": "new",
    "recurring_normal": "normal",
    "requires_investigation": "investigate",
}

# Time-range control options exposed in the UI. "hour" granularity for
# 24h keeps the trend chart readable; day granularity for the wider
# windows avoids an unreadably dense x-axis.
TIME_RANGES = {
    "24h": {"delta": timedelta(hours=24), "granularity": "hour"},
    "7d": {"delta": timedelta(days=7), "granularity": "day"},
    "30d": {"delta": timedelta(days=30), "granularity": "day"},
}

_jira_client = JiraClient()


def _resolve_range(range_key: str) -> tuple[datetime, str]:
    spec = TIME_RANGES.get(range_key, TIME_RANGES["7d"])
    since = datetime.now(timezone.utc) - spec["delta"]
    return since, spec["granularity"]


@router.get("/dashboard")
def dashboard(request: Request, days: int = 7, session: Session = Depends(get_session)):
    """
    Server-rendered first paint (works with JS disabled, and gives the
    page real content before any fetch calls run). The interactive
    filters/charts on top of this hit the JSON endpoints below via fetch
    and re-render client-side -- this initial render always reflects the
    default 7-day, all-verticals view.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    distinct_fingerprints = session.execute(
        select(func.count(func.distinct(AlertEvent.fingerprint_id))).where(
            AlertEvent.occurred_at >= since
        )
    ).scalar_one()

    noise_count = session.execute(
        select(func.count()).select_from(AlertFingerprint).where(
            AlertFingerprint.last_classification == "potential_noise"
        )
    ).scalar_one()

    investigate_count = session.execute(
        select(func.count()).select_from(AlertFingerprint).where(
            AlertFingerprint.last_classification == "requires_investigation"
        )
    ).scalar_one()

    top_recurring = queries.top_recurring_alerts(session, since)
    for a in top_recurring:
        a["classification_css"] = CLASSIFICATION_CSS.get(a["classification"], "normal")

    context = {
        "request": request,
        "window_days": days,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_alerts": queries.total_alerts(session, since),
        "distinct_fingerprints": distinct_fingerprints,
        "noise_count": noise_count,
        "investigate_count": investigate_count,
        "by_vertical": queries.alerts_by_dimension(session, since, AlertEvent.vertical),
        "by_severity": queries.alerts_by_dimension(session, since, AlertEvent.severity),
        "by_channel": queries.notification_channel_split(session, since),
        "trend": queries.daily_frequency_trend(session, days),
        "top_recurring": top_recurring,
        "noisy_alerts": queries.alerts_by_classification(session, "potential_noise"),
        "investigate_alerts": queries.alerts_by_classification(session, "requires_investigation"),
        "verticals": queries.distinct_verticals(session),
        "time_ranges": list(TIME_RANGES.keys()),
    }
    return templates.TemplateResponse(request=request, name="dashboard.html", context=context)


# ---------------------------------------------------------------------
# JSON API endpoints backing the interactive filters/charts. All are
# read-only (GET) and unauthenticated, same as the rest of the dashboard
# and the per-fingerprint stats endpoints -- consistent with the
# decision that read-only diagnostics stay open while actions/config
# require the API key (see app/security/auth.py and the README section
# explaining that boundary).
# ---------------------------------------------------------------------

@router.get("/dashboard/api/verticals")
def api_verticals(session: Session = Depends(get_session)):
    return {"verticals": queries.distinct_verticals(session)}


@router.get("/dashboard/api/summary")
def api_summary(
    range: str = "7d",
    vertical: str | None = None,
    severity: str | None = None,
    session: Session = Depends(get_session),
):
    since, _ = _resolve_range(range)
    return queries.summary_cards(session, since, vertical=vertical, severity=severity)


@router.get("/dashboard/api/trend")
def api_trend(
    range: str = "7d",
    vertical: str | None = None,
    session: Session = Depends(get_session),
):
    since, granularity = _resolve_range(range)
    series = queries.alert_volume_by_vertical_over_time(session, since, granularity)
    if vertical:
        series = {vertical: series.get(vertical, [])}
    return {"granularity": granularity, "series": series}


@router.get("/dashboard/api/priority-distribution")
def api_priority_distribution(
    range: str = "7d",
    vertical: str | None = None,
    session: Session = Depends(get_session),
):
    since, _ = _resolve_range(range)
    dist = queries.priority_distribution_by_vertical(session, since)
    if vertical:
        dist = {vertical: dist.get(vertical, {"P0": 0, "P1": 0, "P2": 0, "P3": 0})}
    return {"distribution": dist}


@router.get("/dashboard/api/processing-time")
def api_processing_time(range: str = "7d", session: Session = Depends(get_session)):
    since, _ = _resolve_range(range)
    return {"avg_seconds_by_vertical": queries.avg_processing_seconds_by_vertical(session, since)}


@router.get("/dashboard/api/incidents")
def api_incidents(
    range: str = "7d",
    vertical: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    limit: int = 20,
    session: Session = Depends(get_session),
):
    since, _ = _resolve_range(range)
    return {
        "incidents": queries.incident_feed(
            session, since, vertical=vertical, severity=severity, status=status, limit=limit
        )
    }


# ---------------------------------------------------------------------
# Actions -- these mutate state, so they require the API key exactly
# like /config and /incidents. "Tweak routing rules for a vertical" uses
# the existing POST /config/verticals endpoint directly from the
# dashboard's UI rather than a new one -- no need to duplicate that
# logic here.
# ---------------------------------------------------------------------

@router.post("/dashboard/actions/{fingerprint_id}/reevaluate", dependencies=[Depends(require_api_key)])
def action_reevaluate(fingerprint_id: int, session: Session = Depends(get_session)):
    return reevaluate_fingerprint(session, fingerprint_id)


@router.post("/dashboard/actions/{incident_id}/sync-jira", dependencies=[Depends(require_api_key)])
def action_sync_jira(incident_id: int, session: Session = Depends(get_session)):
    return sync_jira_status(session, _jira_client, incident_id)
