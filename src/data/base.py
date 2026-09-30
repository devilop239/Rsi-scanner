"""MarketDataProvider — abstract interface (Protocol) for all data sources.

Business logic only depends on this interface, never on yfinance or any
specific broker API. New providers (Fyers, Angel One, NSE bhavcopy) can
be added without touching the scanner or indicator layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass(frozen=True)
class CandleData:
    """Represents a batch of OHLCV data for one ticker.

    Attributes:
        ticker:    The ticker symbol (e.g. "RELIANCE.NS").
        df:        DataFrame with DatetimeIndex and columns:
                   open, high, low, close, adj_close, volume.
                   Index is tz-naive dates in ascending order.
        source:    Name of the provider that fetched this data.
        as_of:     The date this data was fetched.
    """

    ticker: str
    df: pd.DataFrame
    source: str
    as_of: date


@dataclass(frozen=True)
class ProviderHealth:
    """Health snapshot of a data provider."""

    provider_name: str
    is_healthy: bool
    last_check: date
    error: str | None = None


@runtime_checkable
class MarketDataProvider(Protocol):
    """Interface that all market data providers must implement.

    All methods are async. Implementations run blocking calls (like yfinance)
    via asyncio.to_thread so the event loop is never blocked.
    """

    @property
    def name(self) -> str:
        """Human-readable name of this provider (e.g. 'yfinance')."""
        ...

    async def fetch_candles(
        self,
        tickers: list[str],
        start: date,
        end: date,
    ) -> dict[str, CandleData]:
        """Fetch adjusted OHLCV candles for a list of tickers.

        Args:
            tickers: List of ticker symbols (e.g. ["RELIANCE.NS", "TCS.NS"]).
            start:   Start date (inclusive).
            end:     End date (inclusive). Implementations should use completed
                     candles only — never include a partially formed today candle.

        Returns:
            Dict mapping ticker → CandleData. Missing tickers are omitted
            (caller should log and skip them).
        """
        ...

    async def health_check(self) -> ProviderHealth:
        """Check if the provider is reachable and returning sensible data.

        Returns:
            ProviderHealth with is_healthy=True if operational.
        """
        ...
