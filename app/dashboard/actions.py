"""
Manual dashboard actions: re-run triage for a fingerprint on demand, and
pull an incident's status back in sync with Jira. Both are explicit,
human-triggered actions -- consistent with the human-in-the-loop
principle applied everywhere else in this project (the bot recommends
and automates ticket creation, but destructive/authoritative state
changes stay behind an explicit click, not a background job).
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AlertEvent, AlertFingerprint, Incident
from app.jira.client import JiraClient
from app.notification.composer import compose
from app.notification.dispatcher import dispatch, should_notify
from app.parsing.schemas import ParsedAlert
from app.routing.router import resolve_routing
from app.stats.stats_engine import compute_stats
from app.triage.rules_repository import get_active_rules
from app.triage.triage_engine import classify


def reevaluate_fingerprint(session: Session, fingerprint_id: int) -> dict:
    """
    Recomputes stats + triage classification for a fingerprint right now
    (rather than waiting for the next alert occurrence to trigger it),
    using the same rules/routing/notification path the normal pipeline
    uses -- this is not a separate code path, just an on-demand
    invocation of the same steps for the fingerprint's most recent event.
    """
    fingerprint = session.get(AlertFingerprint, fingerprint_id)
    if not fingerprint:
        return {"error": "fingerprint not found"}

    latest_event = session.execute(
        select(AlertEvent)
        .where(AlertEvent.fingerprint_id == fingerprint_id)
        .order_by(AlertEvent.occurred_at.desc())
        .limit(1)
    ).scalars().first()
    if not latest_event:
        return {"error": "no events recorded for this fingerprint"}

    stats = compute_stats(session, fingerprint_id)
    rules = get_active_rules(session)
    triage = classify(stats, rules=rules)

    previous_classification = fingerprint.last_classification
    fingerprint.last_classification = triage.classification
    session.commit()

    routing = resolve_routing(session, fingerprint.vertical, fingerprint.service, fingerprint.alert_name)
    target_channel = routing.devops_channel or routing.dev_channel
    if not target_channel:
        # Same fallback as the live pipeline: notify the source channel
        # rather than silently no-op when routing isn't configured yet.
        target_channel = latest_event.source_channel

    notified = False
    if target_channel and should_notify(session, fingerprint, latest_event.priority, latest_event.severity):
        parsed = ParsedAlert(
            source_channel=latest_event.source_channel,
            raw_message=latest_event.raw_message,
            alert_name=latest_event.alert_name,
            service=latest_event.service,
            vertical=latest_event.vertical,
            environment=latest_event.environment,
            severity=latest_event.severity,
            host_or_pod=latest_event.host_or_pod,
            labels=latest_event.labels or {},
            slack_ts=latest_event.slack_ts,
            slack_thread_ts=latest_event.slack_thread_ts,
            occurred_at=latest_event.occurred_at,
        )
        message = compose(parsed, stats, triage)
        dispatch(
            session=session,
            channel=target_channel,
            message=message,
            fingerprint=fingerprint,
            alert_event_id=latest_event.id,
            classification=triage.classification,
            rationale=triage.rationale,
        )
        notified = True

    return {
        "fingerprint_id": fingerprint_id,
        "previous_classification": previous_classification,
        "new_classification": triage.classification,
        "rationale": triage.rationale,
        "notified": notified,
    }


def sync_jira_status(session: Session, jira_client: JiraClient, incident_id: int) -> dict:
    """
    Pulls the incident's local `status` back in line with Jira's actual
    status category, in case someone resolved (or reopened) the ticket
    directly in Jira rather than through this app's
    POST /incidents/{id}/resolve endpoint.
    """
    incident = session.get(Incident, incident_id)
    if not incident:
        return {"error": "incident not found"}
    if not incident.jira_issue_key or incident.jira_issue_key.startswith("DRYRUN-"):
        return {"error": "incident has no real linked Jira issue (created in dry-run mode)"}

    category = jira_client.get_issue_status_category(incident.jira_issue_key)
    if category is None:
        return {"error": "could not fetch status from Jira -- see server logs for details"}

    changed = False
    if category == "Done" and incident.status != "resolved":
        incident.status = "resolved"
        incident.closed_at = datetime.now(timezone.utc)
        changed = True
    elif category != "Done" and incident.status == "resolved":
        incident.status = "open"
        incident.closed_at = None
        changed = True

    if changed:
        session.commit()

    return {
        "incident_id": incident_id,
        "jira_status_category": category,
        "local_status": incident.status,
        "changed": changed,
    }
