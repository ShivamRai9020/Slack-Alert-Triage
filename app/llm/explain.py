"""
Generates a short, plain-English explanation of a triage decision. This
is presented in Slack as a clearly-labeled AI addendum, never as a
replacement for the deterministic classification/rationale -- the rule
engine's output is the ground truth; the LLM is only asked to restate it
more readably, not to re-decide anything.
"""
from app.llm.client import LLMClient
from app.parsing.schemas import ParsedAlert
from app.stats.stats_engine import AlertStats
from app.triage.triage_engine import TriageResult

_SYSTEM_PROMPT = (
    "You are an SRE assistant helping an on-call engineer quickly understand an "
    "alert triage decision. You will be given the alert's classification, the "
    "deterministic rule that produced it, and its recent occurrence statistics. "
    "Write ONE short sentence (max ~25 words) that helps the engineer decide what "
    "to do next. Do not invent facts, error messages, or root causes that are not "
    "in the provided data. Do not repeat the raw numbers verbatim -- interpret them."
)


def explain(llm_client: LLMClient, alert: ParsedAlert, stats: AlertStats, triage: TriageResult) -> str | None:
    if not llm_client.enabled:
        return None

    user_prompt = (
        f"Alert name: {alert.alert_name or 'unknown'}\n"
        f"Service: {alert.service or 'unknown'}\n"
        f"Vertical: {alert.vertical or 'unknown'}\n"
        f"Severity: {alert.severity or 'unknown'}\n"
        f"Classification: {triage.classification}\n"
        f"Rule rationale: {triage.rationale}\n"
        f"Occurrences -- last 1h: {stats.occurrences_1h}, last 24h: {stats.occurrences_24h}, "
        f"last 7d: {stats.occurrences_7d}\n"
        f"Raw alert text (truncated): {alert.raw_message[:400]}"
    )

    return llm_client.complete(_SYSTEM_PROMPT, user_prompt, max_tokens=80)
