"""
Fingerprinting is the highest-risk design decision in this system: get it
wrong and either (a) genuinely-different alerts get merged into one noisy
bucket, or (b) the same recurring alert gets treated as "new" every time
because some volatile field (a pod ID, an exact metric value, a timestamp)
leaks into the hash.

Strategy: fingerprint = hash(service + alert_name + a normalized, sorted
subset of labels). Volatile fields (host_or_pod, exact metric values,
timestamps) are deliberately EXCLUDED from the hash by default. This is
configurable per-alert-name because "volatile" doesn't generalize —
some teams may genuinely want per-host fingerprints (e.g. "this specific
node is flaky") rather than per-alert-type.
"""
import hashlib
import json

from app.parsing.schemas import ParsedAlert

# Alert names where host/pod SHOULD be part of the identity (i.e. we want
# per-host tracking, not per-alert-type-across-all-hosts). Empty by
# default — add entries as you learn which alerts need this.
HOST_SENSITIVE_ALERT_NAMES: set[str] = set()


def normalize(value: str | None) -> str:
    return (value or "unknown").strip().lower()


def compute_fingerprint(alert: ParsedAlert) -> str:
    key_parts = {
        "service": normalize(alert.service),
        "alert_name": normalize(alert.alert_name),
        "vertical": normalize(alert.vertical),
        "environment": normalize(alert.environment),
    }

    if alert.alert_name and alert.alert_name.lower() in HOST_SENSITIVE_ALERT_NAMES:
        key_parts["host_or_pod"] = normalize(alert.host_or_pod)

    canonical = json.dumps(key_parts, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
