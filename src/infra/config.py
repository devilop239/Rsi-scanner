"""Pydantic Settings – single source of truth for all configuration.

All secrets are loaded from environment variables only.
No defaults for secrets; they must be set explicitly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=_ENV_PATH if _ENV_PATH.exists() else ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Telegram ────────────────────────────────────────────────────────────
    bot_token: str = Field(description="Telegram bot token from @BotFather")
    webhook_base_url: str = Field(
        default="",
        description="Public base URL for the webhook (e.g. https://myapp.onrender.com). "
        "Empty string means long-polling mode.",
    )
    webhook_secret: str = Field(
        default="",
        description="Non-guessable secret sent in X-Telegram-Bot-Api-Secret-Token header",
    )
    webhook_path: str = Field(
        default="/webhook/telegram",
        description="URL path for the webhook endpoint",
    )

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="sqlite+aiosqlite:///./dev.db",
        description="SQLAlchemy async database URL. "
        "Use postgresql+asyncpg://... for production.",
    )

    # ── Access Control ───────────────────────────────────────────────────────
    admin_ids: list[int] = Field(
        default_factory=list,
        description="Comma-separated Telegram user IDs with admin access. "
        "E.g. ADMIN_IDS=123456,789012",
    )
    cron_secret: str = Field(
        description="Bearer token protecting POST /internal/run-scan"
    )

    # ── Indicator Parameters ─────────────────────────────────────────────────
    rsi_length: int = Field(default=14, ge=2, description="RSI period (Wilder smoothing)")
    stoch_length: int = Field(default=14, ge=2, description="Stochastic period applied to RSI")
    stoch_k_smooth: int = Field(default=3, ge=1, description="SMA smoothing for %K")
    stoch_d_smooth: int = Field(default=3, ge=1, description="SMA smoothing for %D")
    signal_line: Literal["k", "d"] = Field(
        default="k",
        description="Which line to use for threshold comparison: 'k' or 'd'",
    )
    stoch_low: float = Field(default=20.0, ge=0, le=100, description="Oversold threshold")
    stoch_high: float = Field(default=80.0, ge=0, le=100, description="Overbought threshold")
    min_candles: int = Field(
        default=100, ge=50, description="Minimum candles required to compute indicator"
    )

    # ── Data / Market Calendar ───────────────────────────────────────────────
    data_lookback_days: int = Field(
        default=200,
        ge=100,
        description="Calendar days of history to fetch for indicator calculation",
    )
    price_tolerance_pct: float = Field(
        default=1.0,
        ge=0,
        description="Max allowed % deviation between primary and fallback data sources",
    )
    nifty50_csv_url: str = Field(
        default="https://www.niftyindices.com/IndexConstituents/ind_nifty50list.csv",
        description="URL to the official NSE Nifty 50 constituents CSV",
    )

    # ── Alerting / Deduplication ─────────────────────────────────────────────
    alert_cooldown_days: int = Field(
        default=7,
        ge=0,
        description="Days before re-alerting for the same stock/zone after re-entry",
    )
    alert_on_exit: bool = Field(
        default=False,
        description="Send a notification when a stock leaves the extreme zone",
    )
    max_ticker_failure_pct: float = Field(
        default=20.0,
        ge=0,
        le=100,
        description="Alert admin if this percentage of tickers fail in a scan run",
    )

    # ── Scheduling ───────────────────────────────────────────────────────────
    tz: str = Field(default="Asia/Kolkata", description="Timezone for scheduling")
    scan_cron: str = Field(
        default="30 16 * * 1-5",
        description="Cron expression for the daily scan (default: Mon-Fri at 16:30 IST)",
    )

    # ── Retention ────────────────────────────────────────────────────────────
    candle_retention_days: int = Field(
        default=730, ge=100, description="Days to retain daily_candles rows"
    )
    indicator_retention_days: int = Field(
        default=365, ge=30, description="Days to retain indicator_values rows"
    )
    scan_run_retention_days: int = Field(
        default=90, ge=7, description="Days to retain scan_runs rows"
    )

    # ── Server ───────────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0", description="Uvicorn bind host")
    port: int = Field(default=8000, ge=1, le=65535, description="Uvicorn bind port")
    log_level: str = Field(default="INFO", description="Logging level")
    environment: Literal["development", "production", "testing"] = Field(
        default="development"
    )

    # ── Rate Limiting ────────────────────────────────────────────────────────
    rate_limit_messages: int = Field(
        default=5, ge=1, description="Max bot messages per user per rate_limit_window_seconds"
    )
    rate_limit_window_seconds: int = Field(
        default=60, ge=1, description="Rate limit sliding window in seconds"
    )

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: object) -> list[int]:
        """Accept comma-separated string from env var or a list."""
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [int(x) for x in v]
        return []

    @field_validator("database_url", mode="after")
    @classmethod
    def fix_postgres_scheme(cls, v: str) -> str:
        """Fix Render's postgres:// or postgresql:// to postgresql+asyncpg:// for SQLAlchemy."""
        if v:
            if v.startswith("postgres://"):
                return v.replace("postgres://", "postgresql+asyncpg://", 1)
            elif v.startswith("postgresql://") and not v.startswith("postgresql+"):
                return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @property
    def is_webhook_mode(self) -> bool:
        """True when running in production webhook mode."""
        return bool(self.webhook_base_url)

    @property
    def full_webhook_url(self) -> str:
        """Fully qualified webhook URL."""
        return self.webhook_base_url.rstrip("/") + self.webhook_path

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


# Module-level singleton — import this everywhere.
settings = Settings()

# Auto-fallback: if we're in development mode and the DB URL points to a
# remote PostgreSQL that this machine can't reach, silently switch to local SQLite.
if settings.environment == "development" and "asyncpg" in settings.database_url:
    import socket
    try:
        # Extract hostname from the database URL
        from urllib.parse import urlparse
        parsed = urlparse(settings.database_url.replace("postgresql+asyncpg://", "postgresql://"))
        host = parsed.hostname or ""
        port = parsed.port or 5432
        socket.getaddrinfo(host, port)
    except (socket.gaierror, OSError):
        import structlog
        _log = structlog.get_logger(__name__)
        _log.warning(
            "postgres_unreachable_falling_back_to_sqlite",
            host=host,
            fallback="sqlite+aiosqlite:///./dev.db",
        )
        settings.database_url = "sqlite+aiosqlite:///./dev.db"

