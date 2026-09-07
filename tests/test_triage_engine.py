from app.stats.stats_engine import AlertStats
from app.triage.triage_engine import classify


def make_stats(**overrides) -> AlertStats:
    defaults = dict(
        fingerprint_id=1,
        occurrences_1h=0,
        occurrences_24h=0,
        occurrences_7d=0,
        occurrences_30d=0,
        first_seen_at=None,
        last_seen_at=None,
        avg_interval_seconds=None,
    )
    defaults.update(overrides)
    return AlertStats(**defaults)


def test_first_occurrence_classified_new():
    stats = make_stats(occurrences_1h=1, occurrences_24h=1, occurrences_7d=1)
    result = classify(stats)
    assert result.classification == "new_unknown"


def test_high_frequency_spike_detected():
    stats = make_stats(occurrences_1h=20, occurrences_24h=20, occurrences_7d=20)
    result = classify(stats)
    assert result.classification == "high_frequency_spike"


def test_potential_noise_detected():
    stats = make_stats(
        occurrences_1h=5, occurrences_24h=30, occurrences_7d=100, avg_interval_seconds=300
    )
    result = classify(stats)
    assert result.classification == "potential_noise"


def test_recurring_normal_when_moderate_volume():
    stats = make_stats(
        occurrences_1h=1, occurrences_24h=3, occurrences_7d=10, avg_interval_seconds=3600
    )
    result = classify(stats)
    assert result.classification == "recurring_normal"


def test_unsupported_condition_key_raises():
    import pytest

    from app.triage.triage_engine import classify as classify_fn

    bad_rules = [{"rule_name": "bad", "classification": "x", "priority": 1, "condition": {"nonsense_key": 1}}]
    with pytest.raises(ValueError):
        classify_fn(make_stats(), rules=bad_rules)
