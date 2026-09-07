"""
Bridges the `triage_rules` table (present in the schema since Phase 1,
but unused until now -- the engine was reading DEFAULT_RULES directly)
to app.triage.triage_engine.classify(). Operators can now tune
thresholds via the DB/API without a redeploy, matching the original
design intent.

A short TTL cache avoids a DB round-trip on every single alert (this
table changes rarely -- operators tuning thresholds, not a hot path) while
still picking up changes within a few seconds rather than requiring a
restart.
"""
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TriageRule
from app.triage.triage_engine import DEFAULT_RULES

_CACHE_TTL_SECONDS = 15
_cache: list[dict] | None = None
_cache_loaded_at: float = 0.0
_lock = threading.Lock()


def _row_to_dict(row: TriageRule) -> dict:
    return {
        "rule_name": row.rule_name,
        "classification": row.classification,
        "priority": row.priority,
        "condition": row.condition_json,
    }


def get_active_rules(session: Session) -> list[dict]:
    global _cache, _cache_loaded_at

    now = time.time()
    with _lock:
        if _cache is not None and (now - _cache_loaded_at) < _CACHE_TTL_SECONDS:
            return _cache

    rows = session.execute(
        select(TriageRule).where(TriageRule.active.is_(True))
    ).scalars().all()

    # Empty table (fresh install, migrations just ran) -- fall back to the
    # in-code defaults rather than classifying everything as
    # "requires_investigation" until someone remembers to seed the table.
    rules = [_row_to_dict(r) for r in rows] if rows else DEFAULT_RULES

    with _lock:
        _cache = rules
        _cache_loaded_at = now

    return rules


def invalidate_cache() -> None:
    """Call after writing to triage_rules via the API so changes apply immediately."""
    global _cache
    with _lock:
        _cache = None


def seed_default_rules_if_empty(session: Session) -> None:
    existing = session.execute(select(TriageRule.id).limit(1)).first()
    if existing:
        return

    for rule in DEFAULT_RULES:
        session.add(
            TriageRule(
                rule_name=rule["rule_name"],
                classification=rule["classification"],
                condition_json=rule["condition"],
                priority=rule["priority"],
                active=True,
            )
        )
    session.commit()
