"""
Orchestrates "should this alert have a tracked incident/Jira ticket, and
if so, is it a new one or an update to an existing one" -- matching the
flow originally sketched:

    New alert -> check historical alerts -> check existing Jira incident
    -> existing ticket? YES: update/reference existing ticket
                        NO:  recommend/create ticket depending on severity

`should_open_incident` is kept as a pure function (no DB/Jira dependency)
so the decision logic is unit-testable without mocking infrastructure.
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AlertEvent, AlertFingerprint, Incident
from app.jira.client import JiraClient

# Classifications severe enough to warrant a tracked incident. Deliberately
# conservative for MVP -- "potential_noise" and "recurring_normal" should
# never auto-create a ticket, or this defeats the purpose of the triage
# engine (it would just move alert-fatigue into Jira instead of Slack).
TICKET_WORTHY_CLASSIFICATIONS = {"high_frequency_spike"}
TICKET_WORTHY_WITH_CRITICAL_SEVERITY = {"requires_investigation"}


def should_open_incident(classification: str, severity: str | None) -> bool:
    if classification in TICKET_WORTHY_CLASSIFICATIONS:
        return True
    if classification in TICKET_WORTHY_WITH_CRITICAL_SEVERITY and (severity or "").upper() == "CRITICAL":
        return True
    return False


@dataclass
class IncidentResult:
    incident: Incident
    is_new: bool


def ensure_incident(
    session: Session,
    jira_client: JiraClient,
    fingerprint: AlertFingerprint,
    alert_event: AlertEvent,
    classification: str,
    rationale: str,
) -> IncidentResult | None:
    """
    Returns None if this alert isn't ticket-worthy. Otherwise returns the
    (possibly newly created) open Incident for this fingerprint.
    """
    if not should_open_incident(classification, alert_event.severity):
        return None

    existing = session.execute(
        select(Incident).where(
            Incident.fingerprint_id == fingerprint.id,
            Incident.status == "open",
        )
    ).scalar_one_or_none()

    if existing:
        jira_client.add_comment(
            existing.jira_issue_key,
            f"Alert recurred: {rationale}\nLatest occurrence: {alert_event.occurred_at.isoformat()}",
        )
        existing.last_linked_alert_event_id = alert_event.id
        session.commit()
        return IncidentResult(incident=existing, is_new=False)

    label = f"alert-fingerprint-{fingerprint.fingerprint_hash[:12]}"
    summary = f"[{classification}] {fingerprint.alert_name or 'Unknown alert'} ({fingerprint.service or 'unknown service'})"
    description = (
        f"Auto-created by Alert Triage Bot.\n\n"
        f"Vertical: {fingerprint.vertical}\nService: {fingerprint.service}\n"
        f"Classification: {classification}\nRationale: {rationale}\n"
        f"First occurrence in this incident window: {alert_event.occurred_at.isoformat()}"
    )
    priority = "High" if classification == "high_frequency_spike" else "Medium"

    created = jira_client.create_issue(
        summary=summary,
        description=description,
        labels=[label, "auto-triaged"],
        priority=priority,
    )

    incident = Incident(
        fingerprint_id=fingerprint.id,
        jira_issue_key=created["key"],
        jira_url=created["url"],
        status="open",
        last_linked_alert_event_id=alert_event.id,
        occurrence_count_at_open=1,
    )
    session.add(incident)
    session.commit()
    session.refresh(incident)
    return IncidentResult(incident=incident, is_new=True)
