from datetime import datetime

from pydantic import BaseModel


class ParsedAlert(BaseModel):
    """
    Structured representation of an alert, regardless of which channel's
    format it was parsed from. Fields the parser couldn't extract stay
    None rather than causing a hard failure — a partially-parsed alert is
    still worth storing and triaging.
    """

    source_channel: str
    raw_message: str
    alert_name: str | None = None
    service: str | None = None
    vertical: str | None = None
    environment: str | None = None
    severity: str | None = None
    # Explicit priority from the alert itself (e.g. "priority: P0"), when
    # the source system provides one. Distinct from the P0-P3 the
    # dashboard derives from `severity` as a fallback for alerts that
    # DON'T carry an explicit priority -- see
    # app/dashboard/queries.py::severity_to_priority(). When both exist,
    # this one (the real, source-provided value) always wins.
    priority: str | None = None
    host_or_pod: str | None = None
    labels: dict = {}
    slack_ts: str | None = None
    slack_thread_ts: str | None = None
    occurred_at: datetime
