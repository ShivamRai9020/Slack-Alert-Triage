import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.correlation.correlate import find_related_alerts
from app.db.models import AlertEvent, AlertFingerprint
from app.fingerprinting.fingerprint import compute_fingerprint
from app.jira.client import JiraClient
from app.jira.service import ensure_incident
from app.llm.client import LLMClient
from app.llm.explain import explain
from app.llm.pattern_detection import suggest_duplicate
from app.notification.composer import compose
from app.notification.dispatcher import dispatch, should_notify
from app.observability.metrics import (
    alerts_classified_total,
    alerts_processed_total,
    incidents_created_total,
    pipeline_duration_seconds,
)
from app.parsing.parser import parse_alert_message
from app.routing.router import infer_vertical_service_from_channel, resolve_routing
from app.slack.channel_names import resolve_channel_name
from app.stats.stats_engine import compute_stats
from app.triage.rules_repository import get_active_rules
from app.triage.triage_engine import classify

logger = logging.getLogger(__name__)

# Single instances reused across requests -- both hold pooled HTTP clients,
# no per-request state.
_jira_client = JiraClient()
_llm_client = LLMClient()


def process_raw_message(
    session: Session,
    channel: str,
    raw_message: str,
    slack_ts: str | None = None,
    slack_thread_ts: str | None = None,
    channel_name_hint: str | None = None,
) -> dict:
    """
    End-to-end processing of a single raw alert message. Returns a summary
    dict for logging/debugging. This function is the single place that
    orchestrates the pipeline -- ingestion sources (Slack today, future
    Alertmanager/PagerDuty webhooks) all call into this same function so
    behavior stays consistent regardless of source.
    """
    with pipeline_duration_seconds.time():
        return _process_raw_message(
            session, channel, raw_message, slack_ts, slack_thread_ts, channel_name_hint
        )


def _process_raw_message(
    session: Session,
    channel: str,
    raw_message: str,
    slack_ts: str | None,
    slack_thread_ts: str | None,
    channel_name_hint: str | None = None,
) -> dict:
    alerts_processed_total.labels(channel=channel).inc()

    # 1. Parse
    parsed = parse_alert_message(channel, raw_message, slack_ts, slack_thread_ts)

    # 1b. Channel-based identification -- PRIMARY source of truth for
    # vertical/service, checked unconditionally (not just when the
    # message text came up empty) and OVERRIDING whatever the text
    # itself claims. You already know your Slack workspace's
    # channel-to-vertical layout (e.g. "pg-aws" belongs to "pg2.0") --
    # that's more reliable than depending on every alerting tool's
    # template consistently including a parseable vertical/team field.
    # Falls back to whatever the text parser found only for channels
    # that haven't been registered in source_channels yet. See
    # app/routing/router.py::infer_vertical_service_from_channel for the
    # full resolution rules.
    channel_inference = infer_vertical_service_from_channel(session, channel)
    # Prefer registered mapping name, then Slack hint, then conversations.info
    # so the dashboard shows #channel-name instead of C0123ABCD.
    source_channel_name = resolve_channel_name(
        channel,
        hint=channel_inference.channel_name or channel_name_hint,
    )

    if channel_inference.vertical:
        if parsed.vertical and parsed.vertical != channel_inference.vertical:
            logger.info(
                "Channel config overriding text-parsed vertical: channel=%s says "
                "vertical=%r, alert text said vertical=%r -- using the channel's.",
                channel,
                channel_inference.vertical,
                parsed.vertical,
            )
        parsed.vertical = channel_inference.vertical
        if channel_inference.service:
            parsed.service = channel_inference.service
        # else: leave parsed.service as whatever the text parser found
        # (channel maps to a vertical shared by multiple services, so it
        # can't disambiguate service on its own).
    # else: this channel isn't registered in source_channels yet --
    # fall through to whatever (if anything) the text parser found.

    # 2. Fingerprint
    fp_hash = compute_fingerprint(parsed)
    fingerprint = session.execute(
        select(AlertFingerprint).where(AlertFingerprint.fingerprint_hash == fp_hash)
    ).scalars().first()

    is_new_fingerprint = fingerprint is None
    if fingerprint is None:
        fingerprint = AlertFingerprint(
            fingerprint_hash=fp_hash,
            alert_name=parsed.alert_name,
            service=parsed.service,
            vertical=parsed.vertical,
            normalized_labels={},
        )
        session.add(fingerprint)
        session.flush()  # assigns fingerprint.id
    else:
        fingerprint.last_seen_at = parsed.occurred_at

    # 3. Persist the raw event
    event = AlertEvent(
        fingerprint_id=fingerprint.id,
        source_channel=parsed.source_channel,
        source_channel_name=source_channel_name,
        raw_message=parsed.raw_message,
        alert_name=parsed.alert_name,
        service=parsed.service,
        vertical=parsed.vertical,
        environment=parsed.environment,
        severity=parsed.severity,
        priority=parsed.priority,
        host_or_pod=parsed.host_or_pod,
        labels=parsed.labels,
        slack_ts=parsed.slack_ts,
        slack_thread_ts=parsed.slack_thread_ts,
        occurred_at=parsed.occurred_at,
    )
    session.add(event)
    session.commit()
    session.refresh(fingerprint)
    session.refresh(event)

    # 4. Stats
    stats = compute_stats(session, fingerprint.id)

    # 5. Triage (rules loaded from the DB -- app/triage/rules_repository.py --
    # falling back to in-code defaults if the table is empty)
    active_rules = get_active_rules(session)
    triage = classify(stats, rules=active_rules)
    fingerprint.last_classification = triage.classification
    alerts_classified_total.labels(classification=triage.classification).inc()

    # 6. Route
    routing = resolve_routing(session, parsed.vertical, parsed.service, parsed.alert_name)

    # 7. Jira / incident tracking (only for ticket-worthy classifications --
    # see should_open_incident in app/jira/service.py). Wrapped defensively:
    # a Jira failure (bad credentials, wrong project key, network issue,
    # rate limit) must never block the Slack notification -- the alert
    # still needs to reach the on-call even if ticket tracking is degraded.
    # This mirrors the same fail-open guarantee already built for the LLM
    # client in app/llm/client.py.
    incident_result = None
    try:
        incident_result = ensure_incident(
            session=session,
            jira_client=_jira_client,
            fingerprint=fingerprint,
            alert_event=event,
            classification=triage.classification,
            rationale=triage.rationale,
        )
    except Exception:
        logger.error(
            "Jira incident handling failed for fingerprint_id=%s classification=%s -- "
            "continuing without a ticket so the Slack notification still goes out.",
            fingerprint.id,
            triage.classification,
            exc_info=True,
        )
        session.rollback()
        # ensure_incident may have partially applied changes before failing
        # (e.g. an existing Incident row committed but the Jira comment
        # call then threw). Re-fetch the fingerprint/event objects against
        # the now-rolled-back session so the rest of the pipeline keeps
        # working with valid, attached instances.
        session.refresh(fingerprint)
        session.refresh(event)

    if incident_result is not None and incident_result.is_new:
        incidents_created_total.inc()

    # 8. Correlation -- only worth the query for alerts serious enough to
    # already be creating noise/tickets; skip for routine recurring alerts.
    related_alerts = []
    if incident_result is not None:
        related = find_related_alerts(
            session=session,
            vertical=parsed.vertical,
            service=parsed.service,
            exclude_fingerprint_id=fingerprint.id,
            now=parsed.occurred_at,
        )
        related_alerts = [r.alert_name or "Unknown alert" for r in related]

    # 9. LLM-assisted duplicate detection -- only for genuinely new
    # fingerprints (first occurrence), to catch cases where the hash-based
    # fingerprint split what's really the same alert type due to wording.
    # Bounded to this one moment so LLM call volume doesn't scale with
    # alert recurrence.
    duplicate_suggestion = None
    if is_new_fingerprint and triage.classification == "new_unknown":
        suggestion = suggest_duplicate(
            session=session,
            llm_client=_llm_client,
            new_alert_name=parsed.alert_name,
            service=parsed.service,
            vertical=parsed.vertical,
        )
        duplicate_suggestion = suggestion.likely_duplicate_of if suggestion else None

    # 10. LLM-generated natural-language explanation, additive to the
    # deterministic rationale (see app/llm/explain.py for the framing).
    llm_explanation = explain(_llm_client, parsed, stats, triage)

    result = {
        "fingerprint_id": fingerprint.id,
        "classification": triage.classification,
        "rationale": triage.rationale,
        "notified": False,
        "jira_issue_key": incident_result.incident.jira_issue_key if incident_result else None,
    }

    # 11. Notify (respecting cooldown). Prefer configured devops/dev
    # channels; if routing is empty, fall back to the source channel so
    # alerts still surface in Slack instead of silently disappearing.
    target_channel = routing.devops_channel or routing.dev_channel
    notify_via = "routing"
    if not target_channel:
        target_channel = channel
        notify_via = "source_channel_fallback"
        logger.warning(
            "No vertical_config notify channel for vertical=%r service=%r "
            "alert_name=%r -- falling back to source channel %s. Add a "
            "Routing Config row to send to a dedicated triage channel.",
            parsed.vertical,
            parsed.service,
            parsed.alert_name,
            channel,
        )

    if target_channel and should_notify(session, fingerprint, parsed.priority, parsed.severity):
        message = compose(
            parsed,
            stats,
            triage,
            jira_url=incident_result.incident.jira_url if incident_result else None,
            jira_is_new=incident_result.is_new if incident_result else None,
            related_alerts=related_alerts,
            llm_explanation=llm_explanation,
            duplicate_suggestion=duplicate_suggestion,
        )
        dispatch(
            session=session,
            channel=target_channel,
            message=message,
            fingerprint=fingerprint,
            alert_event_id=event.id,
            classification=triage.classification,
            rationale=triage.rationale,
            llm_explanation=llm_explanation,
        )
        result["notified"] = True
        result["notified_channel"] = target_channel
        result["notify_via"] = notify_via
    else:
        if target_channel and not should_notify(session, fingerprint, parsed.priority, parsed.severity):
            logger.info(
                "Skipping notify for fingerprint_id=%s channel=%s — priority gate "
                "(P0 debounce / non-P0 spike threshold) last_notified_at=%s.",
                fingerprint.id,
                target_channel,
                fingerprint.last_notified_at,
            )
            result["notify_skipped"] = "cooldown"
        session.commit()

    return result
