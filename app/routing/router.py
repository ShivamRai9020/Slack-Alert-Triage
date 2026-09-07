from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SourceChannel, VerticalConfig


@dataclass
class RoutingTarget:
    dev_channel: str | None
    devops_channel: str | None
    matched_level: str | None = None  # "alert_name" | "service" | "vertical" | None


def resolve_routing(
    session: Session,
    vertical: str | None,
    service: str | None,
    alert_name: str | None = None,
) -> RoutingTarget:
    """
    Resolution precedence, most specific wins:
      1. (vertical, service, alert_name) - a specific alert type override
      2. (vertical, service, NULL)       - service-level default
      3. (vertical, NULL, NULL)          - vertical-level default

    Returns (None, None) if nothing is configured at any level - callers
    must handle "nowhere to notify" explicitly rather than silently
    dropping alerts.
    """
    if service and alert_name:
        row = session.execute(
            select(VerticalConfig).where(
                VerticalConfig.vertical == vertical,
                VerticalConfig.service == service,
                VerticalConfig.alert_name == alert_name,
            )
        ).scalars().first()
        if row:
            return RoutingTarget(row.dev_notify_channel, row.devops_notify_channel, "alert_name")

    if service:
        row = session.execute(
            select(VerticalConfig).where(
                VerticalConfig.vertical == vertical,
                VerticalConfig.service == service,
                VerticalConfig.alert_name.is_(None),
            )
        ).scalars().first()
        if row:
            return RoutingTarget(row.dev_notify_channel, row.devops_notify_channel, "service")

    row = session.execute(
        select(VerticalConfig).where(
            VerticalConfig.vertical == vertical,
            VerticalConfig.service.is_(None),
            VerticalConfig.alert_name.is_(None),
        )
    ).scalars().first()

    if row:
        return RoutingTarget(row.dev_notify_channel, row.devops_notify_channel, "vertical")

    return RoutingTarget(None, None, None)


@dataclass
class ChannelInference:
    vertical: str | None
    service: str | None
    channel_name: str | None
    matched: bool = False


def infer_vertical_service_from_channel(session: Session, channel: str) -> ChannelInference:
    """
    PRIMARY identification mechanism: which vertical/service a channel
    belongs to, as configured by a human in `source_channels` -- checked
    first, and OVERRIDES whatever (if anything) the alert text itself
    claims. Rationale: you already know your Slack workspace's
    channel-to-vertical layout (e.g. "pg-aws" belongs to "pg2.0"), and
    that's a more reliable source of truth than depending on every
    alerting tool's message template consistently including a parseable
    vertical/team field -- some do, many don't. Text-based parsing (see
    app/parsing/profiles.py) is now only a fallback for channels that
    haven't been registered here yet.

    channel_id is the unique key on `source_channels` specifically so
    that many different channels can share the same vertical with no
    conflict -- e.g. both "pg-aws" and "pg-canary" mapping to "pg2.0" is
    just two independent rows, nothing like VerticalConfig's
    (vertical, service, alert_name) uniqueness to work around.
    """
    row = session.execute(
        select(SourceChannel).where(SourceChannel.channel_id == channel)
    ).scalars().first()

    if not row:
        return ChannelInference(vertical=None, service=None, channel_name=None, matched=False)

    return ChannelInference(
        vertical=row.vertical, service=row.service, channel_name=row.channel_name, matched=True
    )
