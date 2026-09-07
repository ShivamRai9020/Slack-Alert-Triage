"""
Prometheus metrics for the pieces you specifically asked to be able to
measure: alerts processed, notifications sent, parse failures, and the
mix of triage classifications over time. Exposed at /metrics in Prometheus
text format.
"""
from prometheus_client import Counter, Histogram

alerts_processed_total = Counter(
    "alert_triage_alerts_processed_total",
    "Total number of raw alert messages processed",
    ["channel"],
)

alerts_classified_total = Counter(
    "alert_triage_alerts_classified_total",
    "Total number of alerts classified, by classification",
    ["classification"],
)

notifications_sent_total = Counter(
    "alert_triage_notifications_sent_total",
    "Total number of Slack notifications dispatched",
    ["channel"],
)

parse_failures_total = Counter(
    "alert_triage_parse_failures_total",
    "Total number of alert messages that failed to parse required fields",
)

incidents_created_total = Counter(
    "alert_triage_incidents_created_total",
    "Total number of Jira incidents auto-created",
)

# In-memory, process-local like the dedup guard it instruments (see
# app/ingestion/dedup_guard.py) -- resets on restart, and does not
# coordinate across multiple app replicas. Counts Slack event deliveries
# dropped as duplicates BEFORE they ever become an AlertEvent row, which
# is why this can't be computed from the database the way cooldown-
# suppressed alerts can (see summary_cards() in app/dashboard/queries.py).
alerts_deduplicated_total = Counter(
    "alert_triage_alerts_deduplicated_total",
    "Total number of duplicate Slack event deliveries dropped before processing",
)

pipeline_duration_seconds = Histogram(
    "alert_triage_pipeline_duration_seconds",
    "End-to-end time to process one alert message through the pipeline",
)
