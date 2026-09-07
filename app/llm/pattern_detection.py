"""
Catches the gap the fingerprinting engine's design note calls out
explicitly: textually different alert names that describe the same
underlying condition ("High CPU - service-x" vs "CPU usage critical on
service-x") get different hashes and are tracked as unrelated alert
types, even though a human would recognize them as one thing.

This only runs for `new_unknown` classifications (first occurrence of a
fingerprint) -- that's the one moment where "is this actually new, or is
it a rewording of something we already track" is a useful question to
ask, and it keeps LLM call volume bounded to first-occurrences rather
than every alert.

This NEVER merges fingerprints automatically -- it only surfaces a
suggestion in the Slack notification for a human to act on (e.g. by
adding the alert name variant to the same HOST_SENSITIVE_ALERT_NAMES-style
config, or updating the parser). Silent auto-merging would violate the
human-in-the-loop constraint on classification decisions.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AlertFingerprint
from app.llm.client import LLMClient

_SYSTEM_PROMPT = (
    "You are helping an SRE alert-triage system detect duplicate alert types. "
    "You will be given a NEW alert name and a list of EXISTING alert names already "
    "tracked for the same service. Reply with ONLY the exact text of one existing "
    "name if it almost certainly describes the same underlying condition as the "
    "new one (e.g. different wording for the same metric/threshold breach), or "
    "reply with exactly the word NONE if none of them clearly match. Do not "
    "explain your answer."
)


@dataclass
class DuplicateSuggestion:
    likely_duplicate_of: str


def suggest_duplicate(
    session: Session,
    llm_client: LLMClient,
    new_alert_name: str | None,
    service: str | None,
    vertical: str | None,
    lookback_hours: int = 24,
    max_candidates: int = 8,
) -> DuplicateSuggestion | None:
    if not llm_client.enabled or not new_alert_name or not service:
        return None

    since = datetime.now() - timedelta(hours=lookback_hours)
    query = select(AlertFingerprint.alert_name).where(
        AlertFingerprint.service == service,
        AlertFingerprint.alert_name.is_not(None),
        AlertFingerprint.alert_name != new_alert_name,
        AlertFingerprint.last_seen_at >= since,
    )
    if vertical:
        query = query.where(AlertFingerprint.vertical == vertical)
    query = query.distinct().limit(max_candidates)

    candidates = [r[0] for r in session.execute(query).all()]
    if not candidates:
        return None

    user_prompt = (
        f"NEW alert name: {new_alert_name}\n"
        f"EXISTING alert names for the same service:\n"
        + "\n".join(f"- {c}" for c in candidates)
    )

    result = llm_client.complete(_SYSTEM_PROMPT, user_prompt, max_tokens=40)
    if not result or result.strip().upper() == "NONE":
        return None

    # Only trust the answer if it's verbatim one of the candidates we sent --
    # protects against the model paraphrasing or hallucinating a name.
    result_clean = result.strip()
    if result_clean in candidates:
        return DuplicateSuggestion(likely_duplicate_of=result_clean)
    return None
