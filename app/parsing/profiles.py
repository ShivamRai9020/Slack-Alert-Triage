"""
Format profiles map an alert message to a set of regex extraction rules.
"""
import re

# Slack Alertmanager templates vary:
#   Priority: P1
#   *Priority:* P1
#   *Priority*:  `P1`
#   Priority:\xa0 P1
# Require :/= after the key so "AWS.MGW.Service 4xx" does not steal service.
_KV = (
    r"\*?{key}\*?\s*[:=]\s*\*?\s*`?(?P<val>{value})`?"
)


def _kv(key: str, value: str) -> re.Pattern:
    return re.compile(_KV.format(key=key, value=value), re.IGNORECASE)


FIELD_PATTERNS = {
    "alert_name": re.compile(r"^\[?\w*\]?\s*([A-Za-z0-9 _.-]+?)(?:\n|$)", re.MULTILINE),
    "service": _kv(r"(?:service|svc|application)", r"[\w.-]+"),
    "vertical": _kv(r"(?:vertical|techteam)", r"[\w.-]+"),
    "environment": _kv(r"(?:env|environment)", r"[\w.-]+"),
    "severity": re.compile(
        r"\[(CRITICAL|WARNING|INFO|WARN)\]|"
        + _KV.format(key=r"severity", value=r"critical|warning|warn|info|error|medium|high|low"),
        re.IGNORECASE,
    ),
    "priority": _kv(r"priority", r"P[0-9]"),
    "host_or_pod": _kv(r"(?:host|pod|instance)", r"[\w.:-]+"),
}

ALERTMANAGER_PATTERNS = {
    # Name may sit inside a Slack link: <http://...|[FIRING:1] Name ...>
    "alert_name": re.compile(
        r"\[(?:FIRING|RESOLVED):\d+\]\s*([A-Za-z][\w.-]*(?:\.[\w.-]+)*)"
    ),
    "service": _kv(r"(?:Application|Service)", r"[\w.-]+"),
    "vertical": _kv(r"techteam", r"[\w.-]+"),
    "environment": _kv(r"(?:env|environment)", r"[\w.-]+"),
    "severity": _kv(r"severity", r"critical|warning|warn|info|error|medium|high|low"),
    "priority": _kv(r"priority", r"P[0-9]"),
    "host_or_pod": _kv(r"instance", r"[\w.:-]+"),
}

_ALERTMANAGER_SIGNATURE = re.compile(r"\[(?:FIRING|RESOLVED):\d+\]")
_CLOUDWATCH_SIGNATURE = re.compile(r"ALARM NAME\s*:", re.IGNORECASE)

CHANNEL_PROFILES: dict[str, dict[str, re.Pattern]] = {}


def profile_for_message(channel: str, raw_message: str) -> dict[str, re.Pattern]:
    if channel in CHANNEL_PROFILES:
        return CHANNEL_PROFILES[channel]
    if _ALERTMANAGER_SIGNATURE.search(raw_message):
        return ALERTMANAGER_PATTERNS
    if _CLOUDWATCH_SIGNATURE.search(raw_message):
        return FIELD_PATTERNS
    return FIELD_PATTERNS


def profile_for_channel(channel: str) -> dict[str, re.Pattern]:
    return CHANNEL_PROFILES.get(channel, FIELD_PATTERNS)
