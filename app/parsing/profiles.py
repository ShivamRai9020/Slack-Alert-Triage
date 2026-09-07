"""
Format profiles map an alert message to a set of regex extraction rules.
Alert messages vary by source system, so rather than one universal
parser regex, each known format gets a profile.

Two ways a message gets matched to a profile:
1. Per-channel override (CHANNEL_PROFILES) -- use when a specific
   channel's format is unique and won't appear elsewhere.
2. Content-based auto-detection (see detect_profile()) -- use when the
   same source system (e.g. Alertmanager) might post to several
   different channels/verticals. Checking channel ID alone would mean
   re-registering the same profile once per channel; sniffing the
   message body for that system's signature works regardless of which
   channel it lands in.

Each profile is a dict of field_name -> compiled regex with exactly one
capture group. Fields not present in a message are left as None — this
is deliberate; a partially-parsed alert is still useful.
"""
import re

# Default / legacy profile: a loosely key:value style test message, e.g.
#   "[CRITICAL] High CPU - service-x
#    service: payments-api
#    vertical: payments
#    env: production
#    host: pod-payments-api-7f9c
# This is what the mock generator (mocks/mock_alert_generator.py) and
# manual test messages have used throughout this project. Real
# Alertmanager alerts (see ALERTMANAGER_PATTERNS below) look nothing
# like this -- kept for backward compatibility with existing tests/mocks,
# not because any real alerting tool actually formats messages this way.
FIELD_PATTERNS = {
    "alert_name": re.compile(r"^\[?\w*\]?\s*([A-Za-z0-9 _-]+?)(?:\n|$)", re.MULTILINE),
    "service": re.compile(r"(?:service|svc)\s*[:=]\s*([\w.-]+)", re.IGNORECASE),
    "vertical": re.compile(r"vertical\s*[:=]\s*([\w.-]+)", re.IGNORECASE),
    "environment": re.compile(r"(?:env|environment)\s*[:=]\s*([\w.-]+)", re.IGNORECASE),
    "severity": re.compile(r"\[(CRITICAL|WARNING|INFO|WARN)\]", re.IGNORECASE),
    "host_or_pod": re.compile(r"(?:host|pod)\s*[:=]\s*([\w.-]+)", re.IGNORECASE),
}

# Real Alertmanager-style profile, e.g.:
#   [FIRING:1] HostHighCpuLoad >75 10.210.9.231:9100 || techteam: pplusprod
#     || severity: critical || priority: P0
#   Labels:
#   bu: | techteam: pplusprod | severity : critical
#   instance: 10.210.9.231:9100
#   Annotations:
#   - description = ... Application = postprocessing_pg2 ... Environment = rtdd
#
# Field mapping decisions (please sanity-check these against how your
# org actually uses these labels -- this is my best-guess reading of one
# sample alert, not something I can verify with certainty):
#   - service    <- Annotations "Application" (the specific app/service)
#   - vertical   <- Labels/title "techteam" (the broader owning team)
#   - if this mapping is backwards for your org (e.g. "techteam" is
#     really the service-level identifier and "bu" should be vertical),
#     swap the two regexes below -- everything downstream (routing,
#     dashboard, fingerprinting) just uses whatever ends up in these
#     two fields, it has no independent opinion on which label means what.
ALERTMANAGER_PATTERNS = {
    "alert_name": re.compile(r"\[(?:FIRING|RESOLVED):\d+\]\s*([A-Za-z][A-Za-z0-9_]*)"),
    "service": re.compile(r"Application\s*[:=]\s*([\w.-]+)", re.IGNORECASE),
    "vertical": re.compile(r"techteam\s*[:=]\s*([\w.-]+)", re.IGNORECASE),
    "environment": re.compile(r"(?:env|environment)\s*[:=]\s*([\w.-]+)", re.IGNORECASE),
    "severity": re.compile(r"severity\s*[:=]\s*(critical|warning|warn|info|error)", re.IGNORECASE),
    "priority": re.compile(r"priority\s*[:=]\s*(P[0-9])", re.IGNORECASE),
    # Alertmanager's "instance" label is host:port, e.g. "10.210.9.231:9100"
    # -- the character class needs ':' added vs. the legacy pattern's.
    "host_or_pod": re.compile(r"instance\s*[:=]\s*([\w.:-]+)", re.IGNORECASE),
}

# Signature substring used to auto-detect an Alertmanager-style message
# regardless of which channel it arrives in. Alertmanager's default Slack
# template always starts firing/resolved alerts this way.
_ALERTMANAGER_SIGNATURE = re.compile(r"\[(?:FIRING|RESOLVED):\d+\]")

# Per-channel overrides. Only needed for a channel whose format doesn't
# match any auto-detected signature above and isn't the legacy default --
# e.g. a Datadog or PagerDuty channel with its own distinct template.
CHANNEL_PROFILES: dict[str, dict[str, re.Pattern]] = {
    # "C0123ABCD": {**FIELD_PATTERNS, "severity": re.compile(r"...")},
}


def profile_for_message(channel: str, raw_message: str) -> dict[str, re.Pattern]:
    """
    Resolution order: explicit per-channel override first (most
    specific), then content-based signature detection, then the legacy
    default as a last resort.
    """
    if channel in CHANNEL_PROFILES:
        return CHANNEL_PROFILES[channel]

    if _ALERTMANAGER_SIGNATURE.search(raw_message):
        return ALERTMANAGER_PATTERNS

    return FIELD_PATTERNS


def profile_for_channel(channel: str) -> dict[str, re.Pattern]:
    """Deprecated: kept only so any external caller not yet updated to
    pass raw_message doesn't break. Prefer profile_for_message()."""
    return CHANNEL_PROFILES.get(channel, FIELD_PATTERNS)
