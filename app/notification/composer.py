"""
Template-based message composer for the MVP. An LLM-based composer will
be added behind the ENABLE_LLM_COMPOSER flag in Phase 4 — this module's
`compose()` signature is the contract that composer must also satisfy,
with a fallback to this template on any LLM failure so the system never
goes silent because a provider had an outage.
"""
from app.config import settings
from app.parsing.schemas import ParsedAlert
from app.stats.stats_engine import AlertStats
from app.triage.triage_engine import TriageResult

CLASSIFICATION_EMOJI = {
    "new_unknown": "🚨",
    "high_frequency_spike": "🔥",
    "potential_noise": "⚠️",
    "recurring_normal": "ℹ️",
    "requires_investigation": "🔎",
}


def compose(
    alert: ParsedAlert,
    stats: AlertStats,
    triage: TriageResult,
    jira_url: str | None = None,
    jira_is_new: bool | None = None,
    related_alerts: list[str] | None = None,
    llm_explanation: str | None = None,
    duplicate_suggestion: str | None = None,
) -> str:
    emoji = CLASSIFICATION_EMOJI.get(triage.classification, "ℹ️")
    title = triage.classification.replace("_", " ").title()

    lines = [
        f"{emoji} *Alert Triage: {title}*",
        "",
        f"*Alert:* {alert.alert_name or 'Unknown'}",
        f"*Service:* {alert.service or 'Unknown'}  |  *Vertical:* {alert.vertical or 'Unknown'}",
    ]

    if alert.priority:
        lines.append(f"*Priority:* {alert.priority}  |  *Severity:* {alert.severity or 'Unknown'}")
    elif alert.severity:
        lines.append(f"*Severity:* {alert.severity}")

    lines += [
        "",
        "*Occurrences:*",
        f"• Last 1h: {stats.occurrences_1h}",
        f"• Last 24h: {stats.occurrences_24h}",
        f"• Last 7d: {stats.occurrences_7d}",
    ]

    if stats.avg_interval_seconds:
        mins = stats.avg_interval_seconds / 60
        lines.append(f"• Avg interval: {mins:.1f} min")

    lines += ["", f"*Recommendation:* {_recommendation_text(triage.classification)}", "", f"_{triage.rationale}_"]

    if jira_url:
        verb = "Tracked in new ticket" if jira_is_new else "Updated existing ticket"
        lines += ["", f"*{verb}:* {jira_url}"]

    if related_alerts:
        lines += ["", f"*⚡ Possibly related (fired in the same vertical in the last {settings.correlation_window_minutes} min):*"]
        lines += [f"• {name}" for name in related_alerts]

    if duplicate_suggestion:
        lines += [
            "",
            f"*🔎 Possible duplicate:* this may be the same underlying condition as "
            f"*\"{duplicate_suggestion}\"*, just worded differently. Consider updating "
            f"the fingerprint config if so. _(AI suggestion -- please verify)_",
        ]

    if llm_explanation:
        lines += ["", f"*🤖 AI summary:* {llm_explanation}"]

    return "\n".join(lines)


def _recommendation_text(classification: str) -> str:
    return {
        "new_unknown": "🔎 No historical pattern — investigation recommended.",
        "high_frequency_spike": "🚨 Rate is well above baseline — possible active incident, immediate investigation recommended.",
        "potential_noise": "🔇 Recurring with short intervals — consider reviewing threshold/silencing.",
        "recurring_normal": "ℹ️ Known recurring pattern — informational only.",
        "requires_investigation": "🔎 Doesn't match a known pattern yet — please review.",
    }.get(classification, "Please review.")
