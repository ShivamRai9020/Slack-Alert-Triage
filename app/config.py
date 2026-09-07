"""
Centralized configuration. All secrets come from environment variables only —
never hardcode tokens, and never store secrets in the database.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_app_token: str = ""  # xapp-... , only needed for local Socket Mode

    # Defaults to a local SQLite file so the app runs with zero external
    # services -- override with a real Postgres URL in .env for anything
    # beyond solo local testing (SQLite has no real concurrent-write
    # story, which matters once more than one process/thread touches it).
    database_url: str = "sqlite:///./app.db"

    app_env: str = "local"
    log_level: str = "INFO"

    # After a non-P0 spike notify, wait this long before notifying again
    # while the spike continues (P0 uses p0_debounce_minutes instead).
    notification_cooldown_minutes: int = 15
    # P0: minimum gap between notifications for the same fingerprint.
    p0_debounce_minutes: int = 2
    # Non-P0: notify only after this many firings in spike_window_minutes.
    spike_threshold: int = 5
    spike_window_minutes: int = 60

    enable_llm_composer: bool = False
    anthropic_api_key: str = ""
    llm_model: str = "llama3.2"
    llm_timeout_seconds: float = 30.0
    # Local dev default; override for anything beyond your own machine
    # (e.g. an in-cluster Ollama service's DNS name in Kubernetes).
    ollama_base_url: str = "http://localhost:11434"

    # Jira integration
    jira_base_url: str = ""       # e.g. https://yourcompany.atlassian.net
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = ""    # e.g. "OPS" -- MVP: single project; per-vertical mapping is a future extension
    jira_default_issue_type: str = "Task"

    # Correlation
    correlation_window_minutes: int = 15

    # API auth -- protects mutating/sensitive endpoints. Leave blank only
    # for local dev; startup logs a loud warning if blank.
    internal_api_key: str = ""

    # Rate limiting
    rate_limit_requests_per_minute: int = 60


settings = Settings()
