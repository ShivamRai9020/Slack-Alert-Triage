"""
Slack Bolt listener. This is the MVP's AlertSource. Kept deliberately thin
— all it does is grab the raw message and channel and hand off to the
pipeline. When we add Alertmanager/PagerDuty webhook sources in a later
phase, they'll be equally thin adapters calling the same
`process_raw_message` pipeline, so triage/routing/notification logic
never has to know or care where an alert came from.
"""
import logging

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from app.config import settings
from app.db.session import SessionLocal
from app.ingestion.dedup_guard import is_duplicate_delivery
from app.ingestion.pipeline import process_raw_message
from app.observability.metrics import alerts_deduplicated_total

logger = logging.getLogger(__name__)

# Placeholders + disabled token verification so the module imports
# cleanly in tests / dry-run when Slack env vars are unset. Real
# Socket Mode still needs real credentials at runtime.
_token = settings.slack_bot_token or "xoxb-test-token-unused"
_signing = settings.slack_signing_secret or "test-signing-secret-unused"
_verify = bool(settings.slack_bot_token)
app = App(
    token=_token,
    signing_secret=_signing,
    token_verification_enabled=_verify,
)

# Subtypes that are never alert content (edits, joins, pins, …).
# Deliberately does NOT include bot_message — Alertmanager and other
# incoming webhooks post as bots with subtype bot_message.
_IGNORED_SUBTYPES = frozenset({
    "message_changed",
    "message_deleted",
    "message_replied",
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
    "channel_name",
    "channel_archive",
    "channel_unarchive",
    "pin_add",
    "pin_remove",
    "file_share",
    "file_comment",
    "file_mention",
    "thread_broadcast",
    "ekm_access_denied",
    "group_join",
    "group_leave",
})


def _extract_message_text(event: dict) -> str:
    """
    Prefer top-level text; if empty (common for Slack apps / Alertmanager
    attachments), fold in attachment fallback/text/pretext/title so the
    parser still sees techteam, priority, etc.
    """
    parts: list[str] = []
    text = (event.get("text") or "").strip()
    if text:
        parts.append(text)

    for att in event.get("attachments") or []:
        for key in ("fallback", "text", "pretext", "title"):
            value = att.get(key)
            if value and value not in parts:
                parts.append(str(value))
        for field in att.get("fields") or []:
            title = field.get("title") or ""
            value = field.get("value") or ""
            if title or value:
                line = f"{title}: {value}".strip(": ")
                if line and line not in parts:
                    parts.append(line)

    # Block Kit: pull plain text from section/mrkdwn blocks when present.
    for block in event.get("blocks") or []:
        if block.get("type") == "section":
            block_text = (block.get("text") or {}).get("text")
            if block_text and block_text not in parts:
                parts.append(block_text)

    return "\n".join(parts).strip()


def _is_own_bot_message(event: dict) -> bool:
    """Skip our own posts so triage notifications don't re-enter the pipeline."""
    if not settings.slack_bot_token:
        return False
    # Bot user id isn't always on the event; compare bot_id / user when present.
    # chat_postMessage from this app sets bot_id; looping those would spam.
    # We also tag via the absence of alert-like content in our composer, but
    # the hard rule is: never re-process messages authored by this app's bot.
    # Slack Bolt doesn't expose bot_user_id on App easily without auth.test;
    # use bot_profile.name / app_id when available, else rely on subtype +
    # a best-effort check: if the message has no bot_id, it's human.
    # Primary loop prevention: messages we post are from our bot_id.
    # Without a cached bot_user_id, skip only when subtype is missing AND
    # we somehow detect ourselves — for safety we skip events that look like
    # our composed notifications (contain the triage header we emit).
    text = event.get("text") or ""
    if "*Alert Triage:" in text or "Alert Triage:" in text or "🤖 AI summary:" in text:
        return True
    return False


@app.event("message")
def handle_message_events(event, say):  # noqa: ARG001 — say required by Bolt signature
    subtype = event.get("subtype")
    if subtype in _IGNORED_SUBTYPES:
        return

    # Allow other bots (Alertmanager, incoming webhooks). Only skip our
    # own triage posts to avoid an infinite notify → ingest loop.
    if _is_own_bot_message(event):
        logger.debug("Skipping own triage notification in channel=%s", event.get("channel"))
        return

    channel = event.get("channel")
    text = _extract_message_text(event)
    ts = event.get("ts")
    thread_ts = event.get("thread_ts")
    # Human-readable channel name when Slack includes it (rare on message
    # events); pipeline also looks up source_channels / falls back to id.
    channel_name = event.get("channel_name") or (event.get("channel_type") and None)

    if not channel or not text:
        logger.info(
            "Skipping empty/incomplete Slack event channel=%s has_text=%s subtype=%s bot_id=%s",
            channel,
            bool(text),
            subtype,
            event.get("bot_id"),
        )
        return

    if is_duplicate_delivery(channel, ts):
        logger.info("Skipping duplicate Slack delivery for channel=%s ts=%s", channel, ts)
        alerts_deduplicated_total.inc()
        return

    session = SessionLocal()
    try:
        result = process_raw_message(
            session=session,
            channel=channel,
            raw_message=text,
            slack_ts=ts,
            slack_thread_ts=thread_ts,
            channel_name_hint=channel_name,
        )
        logger.info("Processed alert: %s", result)
    except Exception:
        logger.exception("Failed to process alert message from channel %s", channel)
    finally:
        session.close()


def start_socket_mode(app_token: str) -> None:
    """
    Socket Mode is the simplest way to run this locally without exposing
    a public HTTP endpoint (no ngrok needed). Requires an app-level token
    (xapp-...) with connections:write scope, generated separately from
    the bot token under 'Socket Mode' in the Slack app settings.
    For production, prefer HTTP Events API behind a real endpoint instead.
    """
    SocketModeHandler(app, app_token).start()
