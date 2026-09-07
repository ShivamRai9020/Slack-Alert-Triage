from unittest.mock import MagicMock, patch

from app.dashboard.queries import severity_to_priority, summary_cards


def test_severity_to_priority_mapping():
    assert severity_to_priority("CRITICAL") == "P0"
    assert severity_to_priority("critical") == "P0"  # case-insensitive
    assert severity_to_priority("WARNING") == "P1"
    assert severity_to_priority("WARN") == "P1"
    assert severity_to_priority("INFO") == "P2"


def test_severity_to_priority_unknown_defaults_to_p3():
    assert severity_to_priority(None) == "P3"
    assert severity_to_priority("") == "P3"
    assert severity_to_priority("SOMETHING_UNRECOGNIZED") == "P3"


def test_summary_cards_separates_cooldown_suppressed_from_deduplicated():
    """
    cooldown_suppressed (DB-derived: ingested minus notified) and
    deduplicated (Prometheus, process-wide) are different in kind and
    must never be silently merged into one number without both being
    independently visible -- this pins that contract.
    """
    session = MagicMock()
    # total_ingested=10, notified_count=7 -> cooldown_suppressed=3
    session.execute.return_value.scalar_one.side_effect = [10, 7, 2]

    with patch("prometheus_client.REGISTRY.get_sample_value", return_value=5.0):
        result = summary_cards(session, since=MagicMock())

    assert result["total_ingested"] == 10
    assert result["cooldown_suppressed"] == 3
    assert result["deduplicated"] == 5
    assert result["suppressed_total"] == 8
    assert result["jira_tickets_created"] == 2


def test_summary_cards_deduplicated_defaults_to_zero_when_metric_unset():
    session = MagicMock()
    session.execute.return_value.scalar_one.side_effect = [4, 4, 0]

    with patch("prometheus_client.REGISTRY.get_sample_value", return_value=None):
        result = summary_cards(session, since=MagicMock())

    assert result["deduplicated"] == 0
    assert result["cooldown_suppressed"] == 0
