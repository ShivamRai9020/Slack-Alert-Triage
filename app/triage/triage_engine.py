"""
Rule-based, deterministic triage classifier. Rules are data (condition
dicts), not hardcoded if/else chains, so thresholds can be tuned by
editing rows in `triage_rules` without a redeploy. This module only knows
how to *evaluate* a condition against computed stats — it doesn't know
Slack, Postgres connection details, or anything else.

Deliberately NO ML/LLM here (see Phase 4 note in project notes) — this
engine must keep working even if an LLM provider is down.
"""
from dataclasses import dataclass

from app.stats.stats_engine import AlertStats

# Supported condition keys. Extend as needed — keep this list small and
# explicit rather than allowing arbitrary code in condition_json.
SUPPORTED_CONDITIONS = {
    "occurrences_1h_gt",
    "occurrences_24h_gt",
    "occurrences_24h_lt",
    "occurrences_7d_gt",
    "is_first_occurrence",
    "avg_interval_seconds_lt",
}


@dataclass
class TriageResult:
    classification: str
    rationale: str
    matched_rule: str | None


# Starter rule set for MVP. In the DB (triage_rules table) these are rows;
# this in-code default is used as a fallback / seed data so the system
# works even before anyone has configured rules via the API.
DEFAULT_RULES = [
    {
        "rule_name": "first_occurrence",
        "classification": "new_unknown",
        "priority": 10,
        "condition": {"is_first_occurrence": True},
    },
    {
        "rule_name": "high_frequency_spike",
        "classification": "high_frequency_spike",
        "priority": 20,
        "condition": {"occurrences_1h_gt": 15},
    },
    {
        "rule_name": "potential_noise",
        "classification": "potential_noise",
        "priority": 30,
        "condition": {"occurrences_24h_gt": 20, "avg_interval_seconds_lt": 600},
    },
    {
        "rule_name": "recurring_normal",
        "classification": "recurring_normal",
        "priority": 40,
        "condition": {"occurrences_24h_gt": 1},
    },
]


def _condition_matches(condition: dict, stats: AlertStats) -> bool:
    for key, threshold in condition.items():
        if key not in SUPPORTED_CONDITIONS:
            raise ValueError(f"Unsupported triage condition key: {key}")

        if key == "is_first_occurrence":
            is_first = stats.occurrences_24h <= 1 and stats.occurrences_7d <= 1
            if bool(threshold) != is_first:
                return False
        elif key == "occurrences_1h_gt":
            if not stats.occurrences_1h > threshold:
                return False
        elif key == "occurrences_24h_gt":
            if not stats.occurrences_24h > threshold:
                return False
        elif key == "occurrences_24h_lt":
            if not stats.occurrences_24h < threshold:
                return False
        elif key == "occurrences_7d_gt":
            if not stats.occurrences_7d > threshold:
                return False
        elif key == "avg_interval_seconds_lt":
            if stats.avg_interval_seconds is None or not stats.avg_interval_seconds < threshold:
                return False
    return True


def classify(stats: AlertStats, rules: list[dict] | None = None) -> TriageResult:
    """
    Evaluate rules in priority order (lowest number = evaluated first,
    i.e. highest precedence). First match wins. Falls back to
    'requires_investigation' if nothing matches, which is the safe
    default — never silently classify as noise by default.
    """
    active_rules = sorted(rules or DEFAULT_RULES, key=lambda r: r["priority"])

    for rule in active_rules:
        if _condition_matches(rule["condition"], stats):
            rationale = _build_rationale(rule["classification"], stats)
            return TriageResult(
                classification=rule["classification"],
                rationale=rationale,
                matched_rule=rule["rule_name"],
            )

    return TriageResult(
        classification="requires_investigation",
        rationale="No historical pattern matched known rules; treating as novel.",
        matched_rule=None,
    )


def _build_rationale(classification: str, stats: AlertStats) -> str:
    return (
        f"{classification}: {stats.occurrences_1h} in last 1h, "
        f"{stats.occurrences_24h} in last 24h, {stats.occurrences_7d} in last 7d"
    )
