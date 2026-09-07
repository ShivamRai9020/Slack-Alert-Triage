from datetime import datetime, timezone

from app.fingerprinting.fingerprint import compute_fingerprint
from app.parsing.schemas import ParsedAlert


def make_alert(**overrides) -> ParsedAlert:
    defaults = dict(
        source_channel="vertical-a-alerts",
        raw_message="test",
        alert_name="High CPU",
        service="payments-api",
        vertical="payments",
        environment="production",
        host_or_pod="pod-abc-123",
        occurred_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ParsedAlert(**defaults)


def test_same_alert_type_different_hosts_share_fingerprint():
    a = make_alert(host_or_pod="pod-abc-123")
    b = make_alert(host_or_pod="pod-xyz-999")
    assert compute_fingerprint(a) == compute_fingerprint(b)


def test_different_services_have_different_fingerprints():
    a = make_alert(service="payments-api")
    b = make_alert(service="checkout-api")
    assert compute_fingerprint(a) != compute_fingerprint(b)


def test_fingerprint_is_case_insensitive():
    a = make_alert(service="Payments-API")
    b = make_alert(service="payments-api")
    assert compute_fingerprint(a) == compute_fingerprint(b)


def test_missing_fields_still_produce_a_fingerprint():
    a = make_alert(service=None, vertical=None, environment=None)
    fp = compute_fingerprint(a)
    assert isinstance(fp, str) and len(fp) == 64
