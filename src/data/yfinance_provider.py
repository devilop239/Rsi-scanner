"""YFinanceProvider — MarketDataProvider implementation using yfinance.

Design decisions:
  - yfinance is synchronous; all calls run via asyncio.to_thread.
  - All 50 tickers are downloaded in ONE batch call (yf.download) to minimise
    the number of HTTP connections and reduce 429 exposure.
  - Tenacity handles retries with exponential backoff + jitter.
  - Adj Close is used exclusively (split- and dividend-adjusted).
  - Tickers ending in .NS handle M&M→M%26M.NS and BAJAJ-AUTO→BAJAJ-AUTO.NS
    transparently — yfinance accepts these as-is.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import yfinance as yf
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
    before_sleep_log,
)

from src.data.base import CandleData, MarketDataProvider, ProviderHealth
from src.infra.logging import get_logger

log = get_logger(__name__)

# Columns yfinance returns in the raw multi-ticker download DataFrame
_YFINANCE_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]

# Internal column name mapping → our canonical names
_COLUMN_MAP = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


def _parse_single_ticker(
    raw: pd.DataFrame,
    ticker: str,
    all_tickers: list[str],
    as_of: date,
    source: str,
) -> CandleData | None:
    """Extract and clean one ticker's data from the batch download result."""
    try:
        if isinstance(raw.columns, pd.MultiIndex):
            if ticker in raw.columns.get_level_values(0):
                df = raw[ticker].copy()
            elif ticker in raw.columns.get_level_values(1):
                df = raw.xs(ticker, level=1, axis=1).copy()
            else:
                return None
        else:
            df = raw.copy()

        # Rename to canonical column names
        df = df.rename(columns=_COLUMN_MAP)
        df.index = pd.to_datetime(df.index).normalize()  # Ensure date-only

        # Fallback if yfinance drops Adj Close
        if "adj_close" not in df.columns and "close" in df.columns:
            df["adj_close"] = df["close"]

        # Keep only columns we care about
        expected = list(_COLUMN_MAP.values())
        present = [c for c in expected if c in df.columns]
        df = df[present]

        # Drop rows where adj_close is NaN (missing trading days)
        if "adj_close" in df.columns:
            df = df[df["adj_close"].notna()]

        if df.empty:
            return None

        # Ensure ascending date order
        df = df.sort_index()
        df.index.name = "date"

        return CandleData(ticker=ticker, df=df, source=source, as_of=as_of)

    except Exception as exc:
        log.error("ticker_parse_error", ticker=ticker, error=str(exc))
        return None


class YFinanceProvider:
    """MarketDataProvider backed by yfinance."""

    name: str = "yfinance"

    def __init__(
        self,
        max_retries: int = 3,
        min_wait_seconds: float = 2.0,
        max_wait_seconds: float = 30.0,
    ) -> None:
        self._max_retries = max_retries
        self._min_wait = min_wait_seconds
        self._max_wait = max_wait_seconds

    def _make_retry_decorator(self) -> Any:
        """Build a tenacity retry decorator with exponential backoff + jitter."""
        return retry(
            retry=retry_if_exception_type((Exception,)),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(
                multiplier=1,
                min=self._min_wait,
                max=self._max_wait,
            ) + wait_random(0, 2),
            before_sleep=before_sleep_log(log, log.warning),  # type: ignore[arg-type]
            reraise=True,
        )

    async def fetch_candles(
        self,
        tickers: list[str],
        start: date,
        end: date,
    ) -> dict[str, CandleData]:
        """Fetch adjusted OHLCV data for all tickers in smaller chunks to avoid OOM."""
        if not tickers:
            return {}

        log.info(
            "fetching_candles",
            provider=self.name,
            ticker_count=len(tickers),
            start=start.isoformat(),
            end=end.isoformat(),
        )

        as_of = date.today()
        results: dict[str, CandleData] = {}
        chunk_size = 5  # Small chunk size to keep memory low

        from datetime import timedelta
        end_exclusive = end + timedelta(days=1)
        start_str = start.strftime("%Y-%m-%d")
        end_str = end_exclusive.strftime("%Y-%m-%d")

        import gc

        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i : i + chunk_size]
            
            @self._make_retry_decorator()
            def _download_chunk() -> pd.DataFrame:
                return yf.download(
                    tickers=chunk,
                    start=start_str,
                    end=end_str,
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )

            try:
                raw = await asyncio.to_thread(_download_chunk)
                if raw.empty:
                    continue
                    
                for ticker in chunk:
                    candle = _parse_single_ticker(
                        raw=raw,
                        ticker=ticker,
                        all_tickers=chunk,
                        as_of=as_of,
                        source=self.name,
                    )
                    if candle is not None:
                        results[ticker] = candle
            except Exception as exc:
                log.error("yfinance_chunk_failed", chunk=chunk, error=str(exc))
                
            # Yield control to event loop and give GC a chance to clear pandas structures
            gc.collect()
            await asyncio.sleep(0.1)

        log.info(
            "candles_fetched",
            provider=self.name,
            requested=len(tickers),
            fetched=len(results),
        )
        return results

    async def health_check(self) -> ProviderHealth:
        """Verify yfinance is operational by fetching one row of NIFTY data."""
        today = date.today()
        start = today - timedelta(days=10)

        try:
            result = await self.fetch_candles(["^NSEI"], start=start, end=today)
            is_healthy = bool(result and not result["^NSEI"].df.empty)
            error = None if is_healthy else "Fetched empty data for ^NSEI"
        except Exception as exc:
            is_healthy = False
            error = str(exc)

        return ProviderHealth(
            provider_name=self.name,
            is_healthy=is_healthy,
            last_check=today,
            error=error,
        )


# ---------------------------------------------------------------------------
# Fallback-aware wrapper
# ---------------------------------------------------------------------------


class FallbackDataProvider:
    """Wraps a primary + optional secondary provider.

    On primary failure, falls back to the secondary and logs a warning.
    Also flags a warning when the latest close prices diverge by more than
    `tolerance_pct` between the two sources.
    """

    name: str = "fallback_wrapper"

    def __init__(
        self,
        primary: MarketDataProvider,
        secondary: MarketDataProvider | None = None,
        tolerance_pct: float = 1.0,
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self._tolerance_pct = tolerance_pct

    async def fetch_candles(
        self,
        tickers: list[str],
        start: date,
        end: date,
    ) -> dict[str, CandleData]:
        result = await self._primary.fetch_candles(tickers, start, end)

        if self._secondary is None:
            return result

        # Fetch secondary for validation even if primary succeeded
        try:
            secondary_result = await self._secondary.fetch_candles(tickers, start, end)
            self._validate_prices(result, secondary_result)
        except Exception as exc:
            log.warning("secondary_provider_health_check_failed", error=str(exc))

        # Fall back if primary returned no data at all
        if not result:
            log.warning(
                "primary_provider_failed_using_secondary",
                primary=self._primary.name,
                secondary=self._secondary.name,
            )
            result = await self._secondary.fetch_candles(tickers, start, end)

        return result

    def _validate_prices(
        self,
        primary: dict[str, CandleData],
        secondary: dict[str, CandleData],
    ) -> None:
        """Warn if any ticker's latest close differs by more than tolerance_pct."""
        for ticker in set(primary.keys()) & set(secondary.keys()):
            p_close = primary[ticker].df["adj_close"].dropna()
            s_close = secondary[ticker].df["adj_close"].dropna()

            if p_close.empty or s_close.empty:
                continue

            p_last = p_close.iloc[-1]
            s_last = s_close.iloc[-1]
            if p_last == 0:
                continue

            diff_pct = abs(p_last - s_last) / p_last * 100.0
            if diff_pct > self._tolerance_pct:
                log.warning(
                    "price_divergence_between_sources",
                    ticker=ticker,
                    primary_close=round(p_last, 2),
                    secondary_close=round(s_last, 2),
                    diff_pct=round(diff_pct, 3),
                    tolerance_pct=self._tolerance_pct,
                )

    async def health_check(self) -> ProviderHealth:
        return await self._primary.health_check()
