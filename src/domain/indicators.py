"""Stochastic RSI indicator — pure, vectorised pandas/NumPy implementation.

Matches TradingView's default StochRSI indicator exactly:
  - RSI(14) with Wilder smoothing: ewm(alpha=1/14, adjust=False)
  - Stochastic of RSI: 100 * (RSI - min(RSI, n)) / (max(RSI, n) - min(RSI, n))
  - %K = SMA(stoch_raw, 3)
  - %D = SMA(%K, 3)

This module has zero I/O. All functions are pure transformations on
pandas Series / DataFrames. They can be unit-tested with known fixtures.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _wilder_rsi(close: pd.Series, length: int) -> pd.Series:
    """Compute RSI using Wilder's smoothing (EWM with adjust=False).

    This matches TradingView's RSI implementation exactly.

    Args:
        close: Series of adjusted close prices, indexed by date.
        length: RSI lookback period (typically 14).

    Returns:
        RSI series with the same index as *close*. The first (length) values
        will be NaN because we need a full window to initialise Wilder smoothing.
    """
    if len(close) < length + 1:
        return pd.Series(np.nan, index=close.index)

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    alpha = 1.0 / length

    # Wilder smoothing = exponentially weighted mean with adjust=False
    avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=length).mean()

    # Avoid division by zero: when avg_loss is 0, RSI = 100
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(avg_loss != 0.0, other=100.0)
    return rsi


def _sma(series: pd.Series, length: int) -> pd.Series:
    """Simple moving average with min_periods=length (NaN until full window)."""
    return series.rolling(window=length, min_periods=length).mean()


def _stochastic_of_series(series: pd.Series, length: int) -> pd.Series:
    """Stochastic normalisation of an arbitrary series.

    raw = 100 * (series - min(series, length)) / (max(series, length) - min(series, length))

    When max == min (flat region), returns 0.0 instead of NaN to avoid
    propagating NaN through the downstream smoothing steps.

    Args:
        series: The input series (e.g., the RSI series).
        length: Rolling window length.

    Returns:
        Stochastic values in [0, 100] or NaN where there is insufficient data.
    """
    rolling_min = series.rolling(window=length, min_periods=length).min()
    rolling_max = series.rolling(window=length, min_periods=length).max()
    denom = rolling_max - rolling_min

    # Where denom == 0 (flat prices), TradingView returns 0.
    raw = np.where(
        denom == 0.0,
        0.0,
        100.0 * (series - rolling_min) / denom,
    )
    result = pd.Series(raw, index=series.index)

    # Propagate NaN from rolling_min / rolling_max into result
    result = result.where(rolling_min.notna(), other=np.nan)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class StochRSIResult:
    """Container for computed Stochastic RSI values.

    Attributes:
        rsi:      Wilder RSI series.
        raw:      Unstochastic (un-smoothed) stochastic of RSI.
        k:        %K = SMA(raw, k_smooth).
        d:        %D = SMA(%K, d_smooth).
    """

    __slots__ = ("rsi", "raw", "k", "d")

    def __init__(
        self,
        rsi: pd.Series,
        raw: pd.Series,
        k: pd.Series,
        d: pd.Series,
    ) -> None:
        self.rsi = rsi
        self.raw = raw
        self.k = k
        self.d = d

    def latest(self) -> dict[str, float | None]:
        """Return the most recent non-NaN values as a plain dict.

        Includes ``prev_k`` / ``prev_d`` (the second-to-last valid values) so
        callers can detect *crosses* into extreme zones rather than persistent
        membership.
        """

        def _last(s: pd.Series) -> float | None:
            val = s.dropna()
            return float(val.iloc[-1]) if not val.empty else None

        def _prev(s: pd.Series) -> float | None:
            val = s.dropna()
            return float(val.iloc[-2]) if len(val) >= 2 else None

        return {
            "rsi": _last(self.rsi),
            "stoch_raw": _last(self.raw),
            "stoch_k": _last(self.k),
            "stoch_d": _last(self.d),
            "prev_k": _prev(self.k),
            "prev_d": _prev(self.d),
        }


def compute_stoch_rsi(
    close: pd.Series,
    *,
    rsi_length: int = 14,
    stoch_length: int = 14,
    k_smooth: int = 3,
    d_smooth: int = 3,
    min_candles: int = 100,
) -> StochRSIResult:
    """Compute Stochastic RSI matching TradingView's default indicator.

    Args:
        close:        Series of *adjusted* close prices. Must be sorted
                      chronologically (oldest first).
        rsi_length:   RSI period (Wilder smoothing). Default 14.
        stoch_length: Stochastic window applied to RSI. Default 14.
        k_smooth:     SMA window for %K. Default 3.
        d_smooth:     SMA window for %D. Default 3.
        min_candles:  Minimum number of non-NaN closes required. Raises
                      ValueError if not met so callers can skip gracefully.

    Returns:
        StochRSIResult with .rsi, .raw, .k, and .d Series.

    Raises:
        ValueError: If close has fewer than min_candles non-NaN values.
        TypeError:  If close is not a pandas Series.
    """
    if not isinstance(close, pd.Series):
        raise TypeError(f"close must be a pandas Series, got {type(close).__name__}")

    # Drop leading/trailing NaN prices but keep internal NaN for safety
    close = close.dropna()

    if len(close) < min_candles:
        raise ValueError(
            f"Insufficient data: need {min_candles} candles, got {len(close)}. "
            "Stock will be skipped."
        )

    rsi = _wilder_rsi(close, rsi_length)
    raw = _stochastic_of_series(rsi, stoch_length)
    k = _sma(raw, k_smooth)
    d = _sma(k, d_smooth)

    return StochRSIResult(rsi=rsi, raw=raw, k=k, d=d)


def classify_signal(
    k: float | None,
    d: float | None,
    prev_k: float | None,
    prev_d: float | None,
    *,
    low_threshold: float = 20.0,
    high_threshold: float = 80.0,
    use_d: bool = False,
) -> str | None:
    """Classify whether StochRSI just *crossed into* an extreme zone.

    Only fires on the **entry cross**, not on sustained membership, to prevent
    repeated alerts when a stock stays oversold/overbought for weeks.

    Args:
        k:              Current %K value.
        d:              Current %D value.
        prev_k:         Previous bar %K value.
        prev_d:         Previous bar %D value.
        low_threshold:  Oversold threshold (default 20).
        high_threshold: Overbought threshold (default 80).
        use_d:          If True, compare %D against thresholds (False → %K).

    Returns:
        ``"OVERSOLD"``, ``"OVERBOUGHT"``, or ``None``.
    """
    curr = d if use_d else k
    prev = prev_d if use_d else prev_k

    if curr is None:
        return None

    # Level-based detection: fire whenever the threshold is breached
    if curr < low_threshold:
        return "OVERSOLD"
    if curr > high_threshold:
        return "OVERBOUGHT"
    return None
