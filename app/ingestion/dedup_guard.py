"""
Slack's Events API redelivers an event if your app doesn't ack within ~3s
(e.g. a slow DB write). Without a guard, a retried delivery would be
processed as a second real occurrence of the alert, inflating stats and
potentially double-notifying. This guards on (channel, slack_ts) — Slack's
own message timestamp is a stable, unique-per-message ID, so a retry of
the same event always carries the same ts.

MVP implementation: in-memory dict with TTL cleanup. This is
process-local, so it does NOT protect against duplicates across multiple
app instances (a horizontal scale-out scenario) — if you deploy more than
one replica, move this to Redis (SETNX with expiry) before relying on it.
"""
import threading
import time

_SEEN: dict[str, float] = {}
_LOCK = threading.Lock()
_TTL_SECONDS = 60 * 10  # Slack retries happen within seconds; 10 min is generous headroom


def _cleanup(now: float) -> None:
    expired = [k for k, ts in _SEEN.items() if now - ts > _TTL_SECONDS]
    for k in expired:
        del _SEEN[k]


def is_duplicate_delivery(channel: str, slack_ts: str | None) -> bool:
    """Returns True if this (channel, slack_ts) was already processed."""
    if not slack_ts:
        # No ts to key on (e.g. manually-posted mock events) — never
        # treat as a duplicate; can't safely dedup without an identity.
        return False

    key = f"{channel}:{slack_ts}"
    now = time.time()

    with _LOCK:
        _cleanup(now)
        if key in _SEEN:
            return True
        _SEEN[key] = now
        return False
