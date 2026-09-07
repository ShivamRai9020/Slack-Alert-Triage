from app.slack.channel_names import display_channel_name, looks_like_channel_id, resolve_channel_name


def test_looks_like_channel_id():
    assert looks_like_channel_id("C0BP4HDTVFW") is True
    assert looks_like_channel_id("pg-aws") is False
    assert looks_like_channel_id(None) is False


def test_resolve_prefers_human_hint(monkeypatch):
    monkeypatch.setattr("app.slack.channel_names.settings.slack_bot_token", "")
    assert resolve_channel_name("C0BP4HDTVFW", hint="pg-aws") == "pg-aws"
    assert display_channel_name("C0BP4HDTVFW", "pg-aws") == "pg-aws"


def test_display_falls_back_to_id_without_token(monkeypatch):
    monkeypatch.setattr("app.slack.channel_names.settings.slack_bot_token", "")
    # clear cache side effects from prior tests by using a fresh id
    assert display_channel_name("C9999999999", "C9999999999") == "C9999999999"
