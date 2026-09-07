from app.parsing.parser import parse_alert_message

REAL_ALERTMANAGER_MESSAGE = """[FIRING:1] HostHighCpuLoad >75 10.210.9.231:9100  ||  techteam: pplusprod   || severity: critical   || priority: P0
Labels:
bu:  | techteam: pplusprod   | severity : critical 

instance: 10.210.9.231:9100

Annotations:
- business_impact = Service may misbehave. Which may leads to request failure. 
- description = Name= rtdd_postprocessingAsg-pg2-canary
 - CPU load is > 75%
 - VALUE = 85.26
 - Application = postprocessing_pg2
 - Environment = rtdd
- job = pplusprod-Instance 
- summary = Host high CPU load (instance 10.210.9.231:9100) - (Name rtdd_postprocessingAsg-pg2-canary) 
"""


def test_real_alertmanager_message_parses_correctly():
    """
    Pinned against an actual production alert (not a synthetic example) --
    if this breaks, real alerts are silently failing to route, which is
    exactly the failure mode this test exists to catch before it reaches
    Slack.
    """
    parsed = parse_alert_message("C_ANY_CHANNEL", REAL_ALERTMANAGER_MESSAGE)

    assert parsed.alert_name == "HostHighCpuLoad"
    assert parsed.service == "postprocessing_pg2"
    assert parsed.vertical == "pplusprod"
    assert parsed.environment == "rtdd"
    assert parsed.severity == "CRITICAL"
    assert parsed.priority == "P0"
    assert parsed.host_or_pod == "10.210.9.231:9100"


def test_alertmanager_format_detected_regardless_of_channel():
    """Content-based detection (not channel ID) is what selects the
    Alertmanager profile -- confirms it works on an arbitrary/unregistered
    channel, which is the whole point of detecting by content."""
    parsed = parse_alert_message("SOME_UNREGISTERED_CHANNEL_ID", REAL_ALERTMANAGER_MESSAGE)
    assert parsed.severity == "CRITICAL"


def test_resolved_variant_also_detected():
    resolved_message = REAL_ALERTMANAGER_MESSAGE.replace("[FIRING:1]", "[RESOLVED:1]")
    parsed = parse_alert_message("C_ANY_CHANNEL", resolved_message)
    assert parsed.alert_name == "HostHighCpuLoad"


def test_legacy_test_format_still_works():
    """The mock generator / manual test messages use a different, older
    format -- confirms adding Alertmanager support didn't regress it."""
    legacy_message = """[WARNING] High CPU - service-x
service: payments-api
vertical: payments
env: production
host: pod-payments-api-7f9c"""

    parsed = parse_alert_message("C_ANY_CHANNEL", legacy_message)
    assert parsed.service == "payments-api"
    assert parsed.vertical == "payments"
    assert parsed.environment == "production"
    assert parsed.severity == "WARNING"
    assert parsed.priority is None  # legacy format never states an explicit priority


def test_missing_fields_stay_none_not_error():
    parsed = parse_alert_message("C_ANY_CHANNEL", "some totally unstructured text with no fields at all")
    assert parsed.service is None
    assert parsed.vertical is None
    assert parsed.priority is None
