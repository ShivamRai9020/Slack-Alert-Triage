"""
Sliding-window rate limiter for /internal/events -- the one endpoint an
external caller (or a misbehaving script) could hammer to flood the
pipeline, create excess Jira tickets, or run up LLM API costs.

Same in-memory, process-local pattern as app/ingestion/dedup_guard.py,
with the same limitation: this does NOT coordinate across multiple app
replicas. At >1 replica, move this to Redis (INCR + EXPIRE) alongside
the dedup guard -- both are flagged together in the README and the k8s
deployment's single-replica note.
"""
import threading
import time
from collections import defaultdict, deque

from app.config import settings

_WINDOW_SECONDS = 60
_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def is_rate_limited(client_key: str) -> bool:
    """
    Returns True if `client_key` (e.g. the API key, or the caller's IP as
    a fallback) has exceeded settings.rate_limit_requests_per_minute in
    the trailing 60 seconds.
    """
    limit = settings.rate_limit_requests_per_minute
    if limit <= 0:
        return False  # 0 or negative disables rate limiting entirely

    now = time.time()

    with _lock:
        window = _hits[client_key]
        while window and now - window[0] > _WINDOW_SECONDS:
            window.popleft()

        if len(window) >= limit:
            return True

        window.append(now)
        return False
