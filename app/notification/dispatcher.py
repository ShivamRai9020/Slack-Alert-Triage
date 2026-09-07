import logging
from datetime import datetime, timedelta, timezone

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AlertEvent, AlertFingerprint, RecommendationLog
from app.observability.metrics import notifications_sent_total

logger = logging.getLogger(__name__)

_client = WebClient(token=settings.slack_bot_token) if settings.slack_bot_token else None

_SEVERITY_PRIORITY = {
    "CRITICAL": "P0",
    "ERROR": "P0",
    "WARNING": "P1",
    "WARN": "P1",
    "INFO": "P2",
}


def effective_priority(priority: str | None, severity: str | None = None) -> str:
    """Normalize to P0–P3. Explicit alert priority wins; else severity map; else P3."""
    if priority:
        return priority.strip().upper()
    if severity:
        return _SEVERITY_PRIORITY.get(severity.strip().upper(), "P3")
    return "P3"


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _occurrences_in_window(session: Session, fingerprint_id: int, window: timedelta) -> int:
    since = datetime.now(timezone.utc) - window
    # Compare naively-compatible: store/query with aware UTC; SQLite may
    # return naive — bind a naive cutoff when needed by stripping.
    since_naive = _naive_utc(since)
    count = session.execute(
        select(func.count())
        .select_from(AlertEvent)
        .where(
            AlertEvent.fingerprint_id == fingerprint_id,
            AlertEvent.occurred_at >= since_naive,
        )
    ).scalar_one()
    return int(count or 0)


def should_notify(
    session: Session,
    fingerprint: AlertFingerprint,
    priority: str | None = None,
    severity: str | None = None,
) -> bool:
    """
    Priority-aware notification gate:

    - P0: notify on (almost) every firing — only a short debounce after the
      last notification to avoid tight loops / duplicate Slack deliveries.
    - P1–P3 / unknown: notify only when this fingerprint has fired at least
      N times in the last W minutes (defaults: 5 in 60). After a notify,
      wait NOTIFICATION_COOLDOWN_MINUTES before notifying again while the
      spike continues.
    """
    level = effective_priority(priority, severity)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    last_notified = fingerprint.last_notified_at
    if last_notified is not None:
        last_notified = _naive_utc(last_notified)

    if level == "P0":
        if last_notified is None:
            return True
        debounce = timedelta(minutes=settings.p0_debounce_minutes)
        return (now - last_notified) > debounce

    # Non-P0: spike threshold in rolling window
    window = timedelta(minutes=settings.spike_window_minutes)
    count = _occurrences_in_window(session, fingerprint.id, window)
    if count < settings.spike_threshold:
        logger.debug(
            "Non-P0 notify skipped fingerprint_id=%s priority=%s count=%s/%s in %sm",
            fingerprint.id,
            level,
            count,
            settings.spike_threshold,
            settings.spike_window_minutes,
        )
        return False

    if last_notified is None:
        return True

    renotify = timedelta(minutes=settings.notification_cooldown_minutes)
    return (now - last_notified) > renotify


def dispatch(
    session: Session,
    channel: str,
    message: str,
    fingerprint: AlertFingerprint,
    alert_event_id: int,
    classification: str,
    rationale: str,
    llm_explanation: str | None = None,
) -> None:
    if _client is None:
        # No token configured (e.g. running tests / mock mode) — log only.
        print(f"[DRY-RUN] Would notify #{channel}:\n{message}\n")
    else:
        try:
            _client.chat_postMessage(channel=channel, text=message)
        except SlackApiError as e:
            # Log loudly and RE-RAISE rather than silently continuing --
            # a failed Slack post should not be recorded as a successful
            # notification (that would permanently poison the cooldown:
            # last_notified_at would be set, and this alert would never
            # be retried). Common causes: `channel` is a name but Slack
            # needs the channel ID for a channel the bot hasn't joined
            # (`channel_not_found`), the bot isn't a member of the
            # channel (`not_in_channel`), or `chat:write` scope is
            # missing/the app needs reinstalling after adding it
            # (`missing_scope`).
            logger.error(
                "Slack chat_postMessage failed for channel=%s: %s (slack_error=%s)",
                channel,
                e,
                e.response.get("error") if e.response else "unknown",
            )
            raise

    notifications_sent_total.labels(channel=channel).inc()

    now = datetime.now(timezone.utc)
    fingerprint.last_notified_at = now
    fingerprint.last_classification = classification

    session.add(
        RecommendationLog(
            fingerprint_id=fingerprint.id,
            alert_event_id=alert_event_id,
            classification=classification,
            rationale=rationale,
            notified_channel=channel,
            notified_at=now,
            llm_explanation=llm_explanation,
        )
    )
    session.commit()
