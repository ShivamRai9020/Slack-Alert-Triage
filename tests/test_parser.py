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


CST_MGW_ALERT = """[FIRING:1] AWS.MGW.Service 4xx Count > 100 (http://10.4.51.224:9093/#/alerts?receiver=cst-devops-slack-notifier-for-oauth-alert-group)
Labels: Techteam: digitalcst | Service: cst_mgw
Priority:\xa0 P1 | Severity: critical
Owner:\xa0 dev-team
Annotations:
\xa0\xa0\xa0description: Status 401 Count > 100 for last 2min API /MerchantService/v1/workflow/lead : Value 287.19.
"""

CST_MGW_SLACK_MRKDWN = """[FIRING:1] AWS.MGW.Service 4xx Count > 100 (http://10.4.51.224:9093/#/alerts?receiver=x)
Labels: *Techteam:* digitalcst | *Service:* cst_mgw
*Priority:* P1 | *Severity:* critical
Owner: *dev-team*
Annotations:
description: Status 401 Count > 100
"""


def test_cst_mgw_alert_parses_priority_p1():
    parsed = parse_alert_message("C_CST", CST_MGW_ALERT)
    assert parsed.priority == "P1"
    assert parsed.severity == "CRITICAL"
    assert parsed.vertical == "digitalcst"
    assert parsed.service == "cst_mgw"
    assert "AWS.MGW.Service" in (parsed.alert_name or "")


def test_cst_mgw_slack_mrkdwn_bold_labels_parse_priority_p1():
    """Slack often wraps keys in *bold* — that used to drop priority to P3."""
    parsed = parse_alert_message("C_CST", CST_MGW_SLACK_MRKDWN)
    assert parsed.priority == "P1"
    assert parsed.severity == "CRITICAL"
    assert parsed.vertical == "digitalcst"
    assert parsed.service == "cst_mgw"


# Exact Slack Events API payload body stored in production app.db (CST MGW).
# Bold wraps the key only (*Priority*:), values sit in backticks.
CST_MGW_SLACK_LIVE = """<http://10.4.51.224:9093/#/alerts?receiver=cst-devops-slack-notifier-for-oauth-alert-group|[FIRING:1] AWS.MGW.Service 4xx Count > 100&gt;
*Labels*: Techteam: `digitalcst` *|* Service: `cst_mgw`
*Priority*:  `P1` *|* Severity: `critical`
*Owner*:  `dev-team`
*Annotations*:
   description: Status `401` Count &gt; 100 for last 2min API `/MerchantService/v1/workflow/lead` : Value `287.19`."""


def test_cst_mgw_live_slack_backticks_and_bold_keys():
    parsed = parse_alert_message("C_CST", CST_MGW_SLACK_LIVE)
    assert parsed.priority == "P1"
    assert parsed.severity == "CRITICAL"
    assert parsed.vertical == "digitalcst"
    assert parsed.service == "cst_mgw"
    assert "AWS.MGW.Service" in (parsed.alert_name or "")


CLOUDWATCH_ALARM = """*ALARM NAME :* `[Resolved]` High-RequestCount-app/ckyc-merchant-infra-alb-prod/eaca159f507a9f41
• *TechTeam :* ckyc-merchant
• *Description :* High RequestCount on ckyc-merchant-infra-alb-prod (&gt;2000 for 15 min)
• Priority: P2    • Severity: Medium
"""


def test_cloudwatch_alarm_parses_techteam_and_priority():
    parsed = parse_alert_message("C_CW", CLOUDWATCH_ALARM)
    assert parsed.priority == "P2"
    assert parsed.vertical == "ckyc-merchant"
    assert parsed.severity == "MEDIUM"
