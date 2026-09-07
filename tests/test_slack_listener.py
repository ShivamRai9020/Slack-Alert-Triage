"""Regression: Alertmanager / bot-posted alerts must enter the pipeline."""

from app.ingestion.slack_listener import _extract_message_text, _IGNORED_SUBTYPES


def test_bot_message_subtype_is_not_ignored():
    assert "bot_message" not in _IGNORED_SUBTYPES


def test_extract_text_from_attachments_when_top_level_empty():
    event = {
        "text": "",
        "attachments": [
            {
                "fallback": "[FIRING:1] HostHighCpuLoad || techteam: pplusprod || priority: P0",
                "text": "Application = postprocessing_pg2",
            }
        ],
    }
    text = _extract_message_text(event)
    assert "HostHighCpuLoad" in text
    assert "techteam: pplusprod" in text
    assert "postprocessing_pg2" in text


def test_extract_prefers_top_level_text():
    event = {"text": "hello alert", "attachments": [{"text": "ignored if top present"}]}
    assert _extract_message_text(event).startswith("hello alert")
