"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── symbols ──────────────────────────────────────────────────────────────
    op.create_table(
        "symbols",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False, comment="yfinance ticker, e.g. RELIANCE.NS"),
        sa.Column("company_name", sa.String(100), nullable=False),
        sa.Column("isin", sa.String(12), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("isin"),
        sa.UniqueConstraint("ticker"),
    )
    op.create_index("ix_symbols_ticker", "symbols", ["ticker"])
    op.create_index("ix_symbols_is_active", "symbols", ["is_active"])

    # ── daily_candles ─────────────────────────────────────────────────────────
    op.create_table(
        "daily_candles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.Column("candle_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("adj_close", sa.Float(), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol_id", "candle_date", name="uq_candle_symbol_date"),
    )
    op.create_index("ix_candle_symbol_date", "daily_candles", ["symbol_id", "candle_date"])
    op.create_index("ix_daily_candles_candle_date", "daily_candles", ["candle_date"])

    # ── indicator_values ──────────────────────────────────────────────────────
    op.create_table(
        "indicator_values",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=False),
        sa.Column("rsi", sa.Float(), nullable=True),
        sa.Column("stoch_raw", sa.Float(), nullable=True),
        sa.Column("stoch_k", sa.Float(), nullable=True),
        sa.Column("stoch_d", sa.Float(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol_id", "value_date", name="uq_indicator_symbol_date"),
    )
    op.create_index("ix_indicator_symbol_date", "indicator_values", ["symbol_id", "value_date"])
    op.create_index("ix_indicator_values_value_date", "indicator_values", ["value_date"])

    # ── signals ───────────────────────────────────────────────────────────────
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column(
            "signal_type",
            sa.Enum("OVERSOLD", "OVERBOUGHT", name="signal_type_enum"),
            nullable=False,
        ),
        sa.Column("stoch_k", sa.Float(), nullable=False),
        sa.Column("stoch_d", sa.Float(), nullable=False),
        sa.Column("rsi", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol_id", "signal_date", "signal_type", name="uq_signal"),
    )
    op.create_index("ix_signal_symbol_date", "signals", ["symbol_id", "signal_date"])

    # ── alerts_sent ───────────────────────────────────────────────────────────
    op.create_table(
        "alerts_sent",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.Column(
            "signal_type",
            sa.Enum("OVERSOLD", "OVERBOUGHT", name="signal_type_enum", create_constraint=False),
            nullable=False,
        ),
        sa.Column("alert_date", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("chat_ids_notified", sa.Integer(), nullable=False, server_default="0",
                  comment="Number of subscribers who received this alert"),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_symbol_type", "alerts_sent", ["symbol_id", "signal_type"])
    op.create_index("ix_alerts_sent_sent_at", "alerts_sent", ["sent_at"])

    # ── subscribers ───────────────────────────────────────────────────────────
    op.create_table(
        "subscribers",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False, comment="Telegram user ID"),
        sa.Column("chat_id", sa.BigInteger(), nullable=False, comment="Telegram chat ID to send messages to"),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("first_name", sa.String(64), nullable=True),
        sa.Column(
            "role",
            sa.Enum("ADMIN", "USER", name="user_role_enum"),
            nullable=False,
            server_default="USER",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("subscribed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("telegram_user_id"),
        sa.UniqueConstraint("chat_id"),
    )
    op.create_index("ix_subscribers_is_active", "subscribers", ["is_active"])

    # ── scan_runs ─────────────────────────────────────────────────────────────
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trading_date", sa.Date(), nullable=True,
                  comment="The market date this scan covers. Unique per successful run."),
        sa.Column(
            "status",
            sa.Enum("RUNNING", "SUCCESS", "FAILED", "SKIPPED", name="scan_status_enum"),
            nullable=False,
            server_default="RUNNING",
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("items_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("signals_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alerts_dispatched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scan_runs_started_at", "scan_runs", ["started_at"])
    op.create_index("ix_scan_runs_trading_date", "scan_runs", ["trading_date"])


def downgrade() -> None:
    op.drop_table("scan_runs")
    op.drop_table("subscribers")
    op.drop_table("alerts_sent")
    op.drop_table("signals")
    op.drop_table("indicator_values")
    op.drop_table("daily_candles")
    op.drop_table("symbols")

    # Drop enums explicitly (required for PostgreSQL)
    op.execute("DROP TYPE IF EXISTS scan_status_enum")
    op.execute("DROP TYPE IF EXISTS user_role_enum")
    op.execute("DROP TYPE IF EXISTS signal_type_enum")
