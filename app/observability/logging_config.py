"""
Structured (JSON) logging so log lines are directly queryable in whatever
log aggregation tool ingests them (CloudWatch, Datadog, Loki, etc.)
without a separate parsing step. Deliberately stdlib-only -- no extra
dependency for something this small.
"""
import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Allow callers to attach structured context via `extra={"context": {...}}`
        if hasattr(record, "context"):
            payload["context"] = record.context
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Quiet noisy third-party loggers down to warnings unless you're
    # actively debugging them.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("slack_bolt").setLevel(logging.WARNING)
