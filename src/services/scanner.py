"""Scanner Service — orchestrates the daily Nifty 50 StochRSI scan.

Responsibilities:
  1. Verify it's a trading day (or force-run)
  2. Ensure idempotency (one scan per trading date)
  3. Fetch price history via the MarketDataProvider
  4. Compute StochRSI for each symbol
  5. Persist candles + indicator values to the database
  6. Detect and record signals
  7. Update the scan_run audit record
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.base import MarketDataProvider
from src.data.calendar import last_trading_day
from src.data.index_repo import get_active_symbols
from src.domain.indicators import classify_signal, compute_stoch_rsi
from src.infra.config import settings
from src.infra.database import DatabaseManager
from src.infra.logging import get_logger
from src.infra.models import (
    DailyCandle,
    IndicatorValue,
    ScanRun,
    ScanStatus,
    Signal,
    SignalType,
    Symbol,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SignalRecord:
    """A detected extreme-zone signal for one stock."""

    ticker: str
    company_name: str
    signal_type: str  # "OVERSOLD" or "OVERBOUGHT"
    stoch_k: float
    stoch_d: float
    rsi: float
    close: float
    trading_date: date


@dataclass
class ScanResult:
    """Summary returned after a complete scan run."""

    trading_date: date
    scan_run_id: int
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    signals: list[SignalRecord] = field(default_factory=list)
    duration_ms: int = 0
    was_skipped: bool = False  # True if non-trading day and not forced


# ---------------------------------------------------------------------------
# Scanner Service
# ---------------------------------------------------------------------------


class ScannerService:
    """Orchestrates the end-to-end daily scan."""

    def __init__(
        self,
        provider: MarketDataProvider,
        db: DatabaseManager,
    ) -> None:
        self._provider = provider
        self._db = db

    async def run_scan(self, *, force: bool = False) -> ScanResult:
        """Execute the full Nifty 50 StochRSI scan.

        Args:
            force: Skip idempotency and trading-day checks. Used by /scan command.

        Returns:
            ScanResult with per-run statistics and detected signals.
        """
        started_at = datetime.now(timezone.utc)
        trading_date = last_trading_day()

        # Guard against future dates — use IST to match market calendar
        import pytz as _pytz
        today_ist = datetime.now(_pytz.timezone("Asia/Kolkata")).date()
        if trading_date > today_ist:
            log.error("trading_date_in_future", trading_date=str(trading_date), today_ist=str(today_ist))
            raise ValueError(f"Trading date {trading_date} is in the future. Check server NTP sync.")

        # ── Idempotency: don't re-run for the same trading date ──────────────
        if not force:
            async with self._db.session() as session:
                existing = await self._get_completed_run(session, trading_date)
                if existing:
                    log.info(
                        "scan_already_ran_today",
                        trading_date=str(trading_date),
                        scan_run_id=existing.id,
                    )
                    return ScanResult(
                        trading_date=trading_date,
                        scan_run_id=existing.id,
                        was_skipped=True,
                    )

        # ── Create scan_run record ────────────────────────────────────────────
        scan_run_id: int
        async with self._db.session() as session:
            scan_run = ScanRun(
                started_at=started_at,
                trading_date=trading_date,
                status=ScanStatus.RUNNING,
            )
            session.add(scan_run)
            await session.flush()
            scan_run_id = scan_run.id

        log.info("scan_started", trading_date=str(trading_date), scan_run_id=scan_run_id)

        result = ScanResult(trading_date=trading_date, scan_run_id=scan_run_id)

        try:
            await self._execute_scan(result, trading_date)
            status = ScanStatus.SUCCESS
        except Exception as exc:
            log.error("scan_failed", error=str(exc), scan_run_id=scan_run_id)
            status = ScanStatus.FAILED
            async with self._db.session() as session:
                await self._update_scan_run(session, scan_run_id, status, result, str(exc))
            raise

        result.duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)

        async with self._db.session() as session:
            await self._update_scan_run(session, scan_run_id, status, result)

        log.info(
            "scan_completed",
            trading_date=str(trading_date),
            scan_run_id=scan_run_id,
            processed=result.processed,
            failed=result.failed,
            signals=len(result.signals),
            duration_ms=result.duration_ms,
        )

        # Warn admin if too many tickers failed
        failure_pct = (result.failed / max(result.processed + result.failed, 1)) * 100
        if failure_pct > settings.max_ticker_failure_pct:
            log.warning(
                "high_ticker_failure_rate",
                failure_pct=round(failure_pct, 1),
                threshold=settings.max_ticker_failure_pct,
            )

        return result

    async def _execute_scan(self, result: ScanResult, trading_date: date) -> None:
        """Inner scan logic: fetch → compute → save → detect signals."""
        # Get symbols from DB
        async with self._db.session() as session:
            symbols = await get_active_symbols(session)

        if not symbols:
            log.warning("no_active_symbols_found")
            return

        tickers = [s.ticker for s in symbols]

        # Fetch price history for all tickers in one batch call
        start_date = trading_date - timedelta(days=settings.data_lookback_days)
        candle_batch = await self._provider.fetch_candles(tickers, start_date, trading_date)

        # Collect DB objects and signals across all symbols, write in one session
        new_candles: list[DailyCandle] = []
        new_indicators: list[IndicatorValue] = []

        async with self._db.session() as session:
            for symbol in symbols:
                ticker = symbol.ticker
                if ticker not in candle_batch:
                    log.warning("ticker_not_in_response", ticker=ticker)
                    result.failed += 1
                    continue

                candle_data = candle_batch[ticker]
                adj_close = candle_data.df.get("adj_close")

                if adj_close is None or adj_close.empty:
                    log.warning("no_adj_close_data", ticker=ticker)
                    result.failed += 1
                    continue

                try:
                    stoch = compute_stoch_rsi(
                        adj_close,
                        rsi_length=settings.rsi_length,
                        stoch_length=settings.stoch_length,
                        k_smooth=settings.stoch_k_smooth,
                        d_smooth=settings.stoch_d_smooth,
                        min_candles=settings.min_candles,
                    )
                except ValueError as exc:
                    log.warning("insufficient_candles", ticker=ticker, reason=str(exc))
                    result.skipped += 1
                    continue
                except Exception as exc:
                    log.error("indicator_compute_error", ticker=ticker, error=str(exc))
                    result.failed += 1
                    continue

                latest = stoch.latest()
                k_val = latest.get("stoch_k")
                d_val = latest.get("stoch_d")
                rsi_val = latest.get("rsi")
                prev_k = latest.get("prev_k")
                prev_d = latest.get("prev_d")

                if k_val is None or d_val is None or rsi_val is None:
                    log.warning("indicator_returned_none", ticker=ticker)
                    result.failed += 1
                    continue

                # Get latest close price
                close_series = candle_data.df["close"] if "close" in candle_data.df.columns else adj_close
                latest_close = float(close_series.dropna().iloc[-1])

                # Collect candle + indicator for batch write
                candle = await self._build_candle(session, symbol.id, candle_data.df, trading_date)
                if candle is not None:
                    new_candles.append(candle)

                indicator = await self._build_indicator(
                    session, symbol.id, trading_date,
                    rsi=rsi_val, stoch_raw=latest.get("stoch_raw"),
                    stoch_k=k_val, stoch_d=d_val,
                )
                if indicator is not None:
                    new_indicators.append(indicator)

                result.processed += 1

                # Cross-based signal classification
                use_d = settings.signal_line == "d"
                signal_type_str = classify_signal(
                    k=k_val, d=d_val,
                    prev_k=prev_k, prev_d=prev_d,
                    low_threshold=settings.stoch_low,
                    high_threshold=settings.stoch_high,
                    use_d=use_d,
                    alert_on_exit=settings.alert_on_exit,
                )

                if signal_type_str:
                    signal_type = SignalType(signal_type_str)
                    await self._save_signal(
                        session, symbol.id, trading_date,
                        signal_type, k_val, d_val, rsi_val, latest_close,
                    )

                    result.signals.append(SignalRecord(
                        ticker=ticker,
                        company_name=symbol.company_name,
                        signal_type=signal_type_str,
                        stoch_k=round(k_val, 2),
                        stoch_d=round(d_val, 2),
                        rsi=round(rsi_val, 2),
                        close=round(latest_close, 2),
                        trading_date=trading_date,
                    ))

                    log.info(
                        "signal_detected",
                        ticker=ticker,
                        signal_type=signal_type_str,
                        stoch_k=round(k_val, 2),
                    )

            # Batch write candles and indicators in one commit
            if new_candles:
                session.add_all(new_candles)
            if new_indicators:
                session.add_all(new_indicators)

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _build_candle(
        self,
        session: AsyncSession,
        symbol_id: int,
        df: "pd.DataFrame",  # type: ignore[name-defined]
        trading_date: date,
    ) -> DailyCandle | None:
        """Upsert the latest daily candle row and return the ORM object."""
        import pandas as pd

        if df.empty:
            return None

        # Normalize index to timezone-naive (midnight) timestamps.
        # Use tz_convert first to avoid TypeError on already tz-aware DatetimeIndex.
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_convert("UTC").tz_localize(None)
        df.index = pd.to_datetime(df.index).normalize()

        # Only save the most recent row for the trading date
        row = df[df.index.date == trading_date]  # type: ignore[attr-defined]
        if row.empty:
            row = df.iloc[[-1]]  # Fall back to last row

        r = row.iloc[0]

        existing = await session.execute(
            select(DailyCandle).where(
                DailyCandle.symbol_id == symbol_id,
                DailyCandle.candle_date == trading_date,
            )
        )
        candle = existing.scalar_one_or_none()

        if candle is None:
            candle = DailyCandle(symbol_id=symbol_id, candle_date=trading_date)

        candle.open = float(r.get("open")) if pd.notna(r.get("open")) else None
        candle.high = float(r.get("high")) if pd.notna(r.get("high")) else None
        candle.low = float(r.get("low")) if pd.notna(r.get("low")) else None
        candle.close = float(r.get("close")) if pd.notna(r.get("close")) else None
        candle.adj_close = float(r.get("adj_close")) if pd.notna(r.get("adj_close")) else None
        candle.volume = int(r.get("volume")) if pd.notna(r.get("volume")) else None
        candle.fetched_at = datetime.now(timezone.utc)
        return candle

    async def _build_indicator(
        self,
        session: AsyncSession,
        symbol_id: int,
        value_date: date,
        *,
        rsi: float,
        stoch_raw: float | None,
        stoch_k: float,
        stoch_d: float,
    ) -> IndicatorValue | None:
        """Upsert the indicator value row for (symbol, date) and return the ORM object."""
        existing = await session.execute(
            select(IndicatorValue).where(
                IndicatorValue.symbol_id == symbol_id,
                IndicatorValue.value_date == value_date,
            )
        )
        iv = existing.scalar_one_or_none()

        if iv is None:
            iv = IndicatorValue(symbol_id=symbol_id, value_date=value_date)

        iv.rsi = rsi
        iv.stoch_raw = stoch_raw
        iv.stoch_k = stoch_k
        iv.stoch_d = stoch_d
        iv.computed_at = datetime.now(timezone.utc)
        return iv

    async def _save_signal(
        self,
        session: AsyncSession,
        symbol_id: int,
        signal_date: date,
        signal_type: SignalType,
        stoch_k: float,
        stoch_d: float,
        rsi: float,
        close: float,
    ) -> None:
        """Insert a signal record (ignore if already exists for same date+type)."""
        existing = await session.execute(
            select(Signal).where(
                Signal.symbol_id == symbol_id,
                Signal.signal_date == signal_date,
                Signal.signal_type == signal_type,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return  # Already recorded

        session.add(Signal(
            symbol_id=symbol_id,
            signal_date=signal_date,
            signal_type=signal_type,
            stoch_k=stoch_k,
            stoch_d=stoch_d,
            rsi=rsi,
            close=close,
        ))

    async def _get_completed_run(
        self, session: AsyncSession, trading_date: date
    ) -> ScanRun | None:
        result = await session.execute(
            select(ScanRun).where(
                ScanRun.trading_date == trading_date,
                ScanRun.status == ScanStatus.SUCCESS,
            )
        )
        return result.scalars().first()

    async def _update_scan_run(
        self,
        session: AsyncSession,
        scan_run_id: int,
        status: ScanStatus,
        result: ScanResult,
        error_message: str | None = None,
    ) -> None:
        scan_run = await session.get(ScanRun, scan_run_id)
        if scan_run is None:
            return
        scan_run.status = status
        scan_run.completed_at = datetime.now(timezone.utc)
        scan_run.duration_ms = result.duration_ms
        scan_run.items_processed = result.processed
        scan_run.items_failed = result.failed
        scan_run.signals_found = len(result.signals)
        scan_run.error_message = error_message
