"""Resolve Slack channel IDs to human-readable names (cached)."""
from __future__ import annotations

import logging
import re
import threading

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from app.config import settings

logger = logging.getLogger(__name__)

_CHANNEL_ID_RE = re.compile(r"^C[A-Z0-9]{8,}$")
_lock = threading.Lock()
_cache: dict[str, str] = {}
_client: WebClient | None = None


def _get_client() -> WebClient | None:
    global _client
    if not settings.slack_bot_token:
        return None
    if _client is None:
        _client = WebClient(token=settings.slack_bot_token)
    return _client


def looks_like_channel_id(value: str | None) -> bool:
    if not value:
        return False
    return bool(_CHANNEL_ID_RE.match(value.strip()))


def resolve_channel_name(channel_id: str, hint: str | None = None) -> str:
    """
    Return a display name for a Slack channel.

    Order: non-ID hint → cache → conversations.info → channel_id fallback.
    Failures never raise — dashboard/ingest must stay up if Slack is down.
    """
    if hint and not looks_like_channel_id(hint):
        with _lock:
            _cache[channel_id] = hint
        return hint

    with _lock:
        cached = _cache.get(channel_id)
    if cached:
        return cached

    client = _get_client()
    if client is None:
        return hint or channel_id

    try:
        resp = client.conversations_info(channel=channel_id)
        name = (resp.get("channel") or {}).get("name")
        if name:
            with _lock:
                _cache[channel_id] = name
            return name
    except SlackApiError as e:
        logger.warning(
            "conversations.info failed for %s: %s",
            channel_id,
            e.response.get("error") if e.response else e,
        )
    except Exception:
        logger.exception("Unexpected error resolving channel name for %s", channel_id)

    return hint or channel_id


def display_channel_name(channel_id: str | None, stored_name: str | None = None) -> str:
    """Dashboard helper: prefer a real name, resolve if we only have an ID."""
    if stored_name and not looks_like_channel_id(stored_name):
        return stored_name
    if not channel_id:
        return stored_name or "Unknown"
    return resolve_channel_name(channel_id, hint=stored_name)
