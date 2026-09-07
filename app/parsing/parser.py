from datetime import datetime, timezone

from app.parsing.profiles import profile_for_message
from app.parsing.schemas import ParsedAlert


def parse_alert_message(
    channel: str,
    raw_message: str,
    slack_ts: str | None = None,
    slack_thread_ts: str | None = None,
) -> ParsedAlert:
    """
    Extract structured fields from a raw Slack alert message using the
    format profile that matches this message (see
    app/parsing/profiles.py::profile_for_message for the resolution
    order). Never raises on missing fields — an alert with only
    `raw_message` populated is still stored and can be triaged
    (conservatively, as "unknown") rather than dropped.
    """
    patterns = profile_for_message(channel, raw_message)
    extracted: dict[str, str | None] = {}

    for field, pattern in patterns.items():
        match = pattern.search(raw_message)
        extracted[field] = match.group(1).strip() if match else None

    severity = extracted.get("severity")
    if severity:
        severity = severity.upper()

    priority = extracted.get("priority")
    if priority:
        priority = priority.upper()

    return ParsedAlert(
        source_channel=channel,
        raw_message=raw_message,
        alert_name=extracted.get("alert_name"),
        service=extracted.get("service"),
        vertical=extracted.get("vertical"),
        environment=extracted.get("environment"),
        severity=severity,
        priority=priority,
        host_or_pod=extracted.get("host_or_pod"),
        labels={},
        slack_ts=slack_ts,
        slack_thread_ts=slack_thread_ts,
        occurred_at=datetime.now(timezone.utc),
    )
