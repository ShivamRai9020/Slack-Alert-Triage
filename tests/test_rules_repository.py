from unittest.mock import MagicMock

from app.triage import rules_repository
from app.triage.rules_repository import DEFAULT_RULES, get_active_rules, invalidate_cache


def setup_function():
    invalidate_cache()


def test_falls_back_to_default_rules_when_table_empty():
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = []

    rules = get_active_rules(session)

    assert rules == DEFAULT_RULES


def test_uses_db_rules_when_present():
    fake_row = MagicMock()
    fake_row.rule_name = "custom_rule"
    fake_row.classification = "potential_noise"
    fake_row.priority = 5
    fake_row.condition_json = {"occurrences_24h_gt": 50}

    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = [fake_row]

    rules = get_active_rules(session)

    assert len(rules) == 1
    assert rules[0]["rule_name"] == "custom_rule"
    assert rules[0]["condition"] == {"occurrences_24h_gt": 50}


def test_cache_avoids_second_db_call_within_ttl():
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = []

    get_active_rules(session)
    get_active_rules(session)

    assert session.execute.call_count == 1


def test_invalidate_cache_forces_reload():
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = []

    get_active_rules(session)
    invalidate_cache()
    get_active_rules(session)

    assert session.execute.call_count == 2
