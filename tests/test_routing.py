import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import SourceChannel, VerticalConfig
from app.routing.router import infer_vertical_service_from_channel, resolve_routing


@pytest.fixture
def session():
    # In-memory SQLite is sufficient here since these tables have no
    # Postgres-specific (JSONB) columns.
    engine = create_engine("sqlite:///:memory:")
    VerticalConfig.__table__.create(engine)
    SourceChannel.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_falls_back_to_vertical_level_default(session):
    session.add(
        VerticalConfig(
            vertical="payments",
            service=None,
            alert_name=None,
            alert_channel="vertical-a-alerts",
            dev_notify_channel="payments-dev",
            devops_notify_channel="payments-devops",
        )
    )
    session.commit()

    result = resolve_routing(session, "payments", "checkout-api", "High CPU")
    assert result.matched_level == "vertical"
    assert result.devops_channel == "payments-devops"


def test_service_level_overrides_vertical_level(session):
    session.add_all([
        VerticalConfig(vertical="payments", service=None, alert_name=None,
                        alert_channel="c", devops_notify_channel="payments-devops"),
        VerticalConfig(vertical="payments", service="checkout-api", alert_name=None,
                        alert_channel="c", devops_notify_channel="checkout-devops"),
    ])
    session.commit()

    result = resolve_routing(session, "payments", "checkout-api", "High CPU")
    assert result.matched_level == "service"
    assert result.devops_channel == "checkout-devops"


def test_alert_name_level_overrides_service_level(session):
    session.add_all([
        VerticalConfig(vertical="payments", service="checkout-api", alert_name=None,
                        alert_channel="c", devops_notify_channel="checkout-devops"),
        VerticalConfig(vertical="payments", service="checkout-api", alert_name="Database connection failures",
                        alert_channel="c", devops_notify_channel="dba-oncall"),
    ])
    session.commit()

    result = resolve_routing(session, "payments", "checkout-api", "Database connection failures")
    assert result.matched_level == "alert_name"
    assert result.devops_channel == "dba-oncall"


def test_no_config_returns_none_channels(session):
    result = resolve_routing(session, "unmapped-vertical", "some-service", "Some Alert")
    assert result.dev_channel is None
    assert result.devops_channel is None
    assert result.matched_level is None


# ---------------------------------------------------------------------
# infer_vertical_service_from_channel -- PRIMARY channel-to-vertical
# identification, backed by the dedicated source_channels table.
# ---------------------------------------------------------------------

def test_infers_vertical_and_service_from_registered_channel(session):
    session.add(
        SourceChannel(channel_id="C_CHECKOUT", channel_name="checkout-alerts",
                      vertical="payments", service="checkout-api")
    )
    session.commit()

    result = infer_vertical_service_from_channel(session, "C_CHECKOUT")
    assert result.vertical == "payments"
    assert result.service == "checkout-api"
    assert result.channel_name == "checkout-alerts"
    assert result.matched is True


def test_infers_vertical_only_when_channel_has_no_service_set(session):
    session.add(SourceChannel(channel_id="C_PG_AWS", channel_name="pg-aws", vertical="pg2.0"))
    session.commit()

    result = infer_vertical_service_from_channel(session, "C_PG_AWS")
    assert result.vertical == "pg2.0"
    assert result.service is None


def test_multiple_channels_can_map_to_the_same_vertical_without_conflict(session):
    """
    This is the exact scenario VerticalConfig's (vertical, service,
    alert_name) uniqueness couldn't represent: two different channels
    ("pg-aws" and "pg-canary"), neither with a service override, both
    belonging to the same vertical ("pg2.0"). channel_id being the
    unique key here means both rows coexist with zero conflict.
    """
    session.add_all([
        SourceChannel(channel_id="C_PG_AWS", channel_name="pg-aws", vertical="pg2.0"),
        SourceChannel(channel_id="C_PG_CANARY", channel_name="pg-canary", vertical="pg2.0"),
    ])
    session.commit()  # must not raise an IntegrityError

    result_a = infer_vertical_service_from_channel(session, "C_PG_AWS")
    result_b = infer_vertical_service_from_channel(session, "C_PG_CANARY")

    assert result_a.vertical == "pg2.0"
    assert result_b.vertical == "pg2.0"
    assert result_a.channel_name == "pg-aws"
    assert result_b.channel_name == "pg-canary"


def test_returns_nothing_for_unconfigured_channel(session):
    result = infer_vertical_service_from_channel(session, "C_UNKNOWN")
    assert result.vertical is None
    assert result.service is None
    assert result.matched is False
