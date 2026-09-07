from app.ingestion.dedup_guard import _SEEN, is_duplicate_delivery


def setup_function():
    _SEEN.clear()


def test_first_delivery_not_duplicate():
    assert is_duplicate_delivery("alerts-a", "1699999999.000100") is False


def test_repeated_delivery_is_duplicate():
    is_duplicate_delivery("alerts-a", "1699999999.000100")
    assert is_duplicate_delivery("alerts-a", "1699999999.000100") is True


def test_same_ts_different_channel_not_duplicate():
    is_duplicate_delivery("alerts-a", "1699999999.000100")
    assert is_duplicate_delivery("alerts-b", "1699999999.000100") is False


def test_missing_ts_never_flagged_duplicate():
    assert is_duplicate_delivery("alerts-a", None) is False
    assert is_duplicate_delivery("alerts-a", None) is False
