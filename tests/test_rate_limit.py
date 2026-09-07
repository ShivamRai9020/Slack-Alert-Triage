from app.config import settings
from app.security.rate_limit import _hits, is_rate_limited


def setup_function():
    _hits.clear()


def test_allows_requests_under_the_limit():
    settings.rate_limit_requests_per_minute = 5
    for _ in range(5):
        assert is_rate_limited("client-a") is False


def test_blocks_requests_over_the_limit():
    settings.rate_limit_requests_per_minute = 3
    for _ in range(3):
        is_rate_limited("client-a")
    assert is_rate_limited("client-a") is True


def test_different_clients_tracked_independently():
    settings.rate_limit_requests_per_minute = 1
    assert is_rate_limited("client-a") is False
    assert is_rate_limited("client-b") is False
    assert is_rate_limited("client-a") is True


def test_zero_or_negative_limit_disables_limiting():
    settings.rate_limit_requests_per_minute = 0
    for _ in range(100):
        assert is_rate_limited("client-a") is False
