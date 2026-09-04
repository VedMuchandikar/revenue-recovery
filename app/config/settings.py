"""Application settings using Pydantic Settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = "sqlite+aiosqlite:///./revenue_recovery.db"

    # Razorpay
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # Anthropic Claude
    anthropic_api_key: str = ""
    planner_use_llm: bool = True

    # Recovery Configuration
    max_auto_recovery_amount: int = 50000
    max_attempts: int = 3
    cooldown_minutes: int = 60
    claude_confidence_threshold: float = 0.75
    mandate_max_retries: int = 4
    mandate_retry_window_days: int = 30

    # Worker Configuration
    processing_stale_minutes: int = 10
    worker_poll_interval_seconds: int = 2


# Webhook Configuration
webhook_base_url: str = "http://localhost:8000"


settings = Settings()
