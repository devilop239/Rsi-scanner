"""SQLAlchemy ORM models (all tables in one module for easy Alembic autogenerate).

Every timestamp column uses UTC. Application code is responsible for
converting IST → UTC before insertion.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infra.database import Base


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SignalType(str, enum.Enum):
    OVERSOLD = "OVERSOLD"
    OVERBOUGHT = "OVERBOUGHT"


class ScanStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # Non-trading day


class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    USER = "USER"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Symbol(Base):
    """Represents one Nifty 50 constituent ticker."""

    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True,
        comment="yfinance ticker, e.g. RELIANCE.NS",
    )
    company_name: Mapped[str] = mapped_column(String(100), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(12), unique=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    candles: Mapped[list[DailyCandle]] = relationship("DailyCandle", back_populates="symbol")
    indicators: Mapped[list[IndicatorValue]] = relationship(
        "IndicatorValue", back_populates="symbol"
    )
    signals: Mapped[list[Signal]] = relationship("Signal", back_populates="symbol")
    alerts_sent: Mapped[list[AlertSent]] = relationship("AlertSent", back_populates="symbol")

    def __repr__(self) -> str:
        return f"<Symbol {self.ticker}>"


class DailyCandle(Base):
    """One row per (symbol, trading_date) with OHLCV and adjusted close."""

    __tablename__ = "daily_candles"
    __table_args__ = (
        UniqueConstraint("symbol_id", "candle_date", name="uq_candle_symbol_date"),
        Index("ix_candle_symbol_date", "symbol_id", "candle_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False
    )
    candle_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    adj_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    symbol: Mapped[Symbol] = relationship("Symbol", back_populates="candles")

    def __repr__(self) -> str:
        return f"<DailyCandle symbol_id={self.symbol_id} date={self.candle_date}>"


class IndicatorValue(Base):
    """Computed StochRSI indicator values for one (symbol, date)."""

    __tablename__ = "indicator_values"
    __table_args__ = (
        UniqueConstraint("symbol_id", "value_date", name="uq_indicator_symbol_date"),
        Index("ix_indicator_symbol_date", "symbol_id", "value_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False
    )
    value_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    rsi: Mapped[float | None] = mapped_column(Float, nullable=True)
    stoch_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    stoch_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    stoch_d: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    symbol: Mapped[Symbol] = relationship("Symbol", back_populates="indicators")

    def __repr__(self) -> str:
        return f"<IndicatorValue symbol_id={self.symbol_id} date={self.value_date} K={self.stoch_k:.2f}>"


class Signal(Base):
    """A detected oversold/overbought signal on a given date."""

    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("symbol_id", "signal_date", "signal_type", name="uq_signal"),
        Index("ix_signal_symbol_date", "symbol_id", "signal_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False
    )
    signal_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    signal_type: Mapped[SignalType] = mapped_column(
        Enum(SignalType, name="signal_type_enum"), nullable=False
    )
    stoch_k: Mapped[float] = mapped_column(Float, nullable=False)
    stoch_d: Mapped[float] = mapped_column(Float, nullable=False)
    rsi: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    symbol: Mapped[Symbol] = relationship("Symbol", back_populates="signals")

    def __repr__(self) -> str:
        return f"<Signal {self.signal_type} symbol_id={self.symbol_id} date={self.signal_date}>"


class AlertSent(Base):
    """Deduplication log: tracks which alerts have been dispatched."""

    __tablename__ = "alerts_sent"
    __table_args__ = (
        Index("ix_alert_symbol_type", "symbol_id", "signal_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False
    )
    signal_type: Mapped[SignalType] = mapped_column(
        Enum(SignalType, name="signal_type_enum", create_constraint=False), nullable=False
    )
    alert_date: Mapped[date] = mapped_column(Date, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True
    )
    chat_ids_notified: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False,
        comment="Number of subscribers who received this alert",
    )

    symbol: Mapped[Symbol] = relationship("Symbol", back_populates="alerts_sent")

    def __repr__(self) -> str:
        return f"<AlertSent symbol_id={self.symbol_id} type={self.signal_type} date={self.alert_date}>"


class Subscriber(Base):
    """Telegram subscribers who receive automated alerts."""

    __tablename__ = "subscribers"

    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, comment="Telegram user ID"
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True, comment="Telegram chat ID to send messages to"
    )
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role_enum"), default=UserRole.USER, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    subscribed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<Subscriber uid={self.telegram_user_id} role={self.role}>"


class ScanRun(Base):
    """Audit trail for every scan execution."""

    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trading_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True,
        comment="The market date this scan covers. Unique per successful run.",
    )
    status: Mapped[ScanStatus] = mapped_column(
        Enum(ScanStatus, name="scan_status_enum"), nullable=False, default=ScanStatus.RUNNING
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    items_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    signals_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alerts_dispatched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ScanRun id={self.id} status={self.status} date={self.trading_date}>"
