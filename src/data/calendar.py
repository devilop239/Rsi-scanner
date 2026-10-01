"""Market calendar — determines whether a given date is an NSE trading day.

Loads the holiday list from docs/holidays.json (relative to the project root).
Weekends and listed holidays are non-trading days.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from src.infra.logging import get_logger

log = get_logger(__name__)

_HOLIDAYS_PATH = Path(__file__).parent.parent.parent / "docs" / "holidays.json"


@lru_cache(maxsize=1)
def _load_holidays() -> frozenset[date]:
    """Load and cache the NSE holiday set from disk."""
    try:
        with open(_HOLIDAYS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        holidays = frozenset(
            date.fromisoformat(h["date"]) for h in data.get("holidays", [])
        )
        log.info("holidays_loaded", count=len(holidays))
        return holidays
    except FileNotFoundError:
        log.warning("holidays_file_not_found", path=str(_HOLIDAYS_PATH))
        return frozenset()
    except Exception as exc:
        log.error("holidays_load_error", error=str(exc))
        return frozenset()


def is_trading_day(d: date | None = None) -> bool:
    """Return True if *d* is a valid NSE trading day (not weekend, not holiday).

    Args:
        d: Date to check. Defaults to today in Asia/Kolkata.
    """
    if d is None:
        import pytz
        from datetime import datetime
        d = datetime.now(pytz.timezone("Asia/Kolkata")).date()

    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return d not in _load_holidays()


def last_trading_day(reference: date | None = None) -> date:
    """Return the most recent completed trading day on or before *reference*.

    If reference is not provided, it defaults to today if the current time
    is after 16:00 IST (market closed), otherwise defaults to yesterday.

    Args:
        reference: Starting point for the search.
    """
    import pytz
    from datetime import datetime

    if reference is None:
        now = datetime.now(pytz.timezone("Asia/Kolkata"))
        # NSE closes at 15:30 IST. EOD data is usually available by 16:00 IST.
        if now.hour >= 16:
            reference = now.date()
        else:
            reference = now.date() - timedelta(days=1)

    candidate = reference
    for _ in range(14):  # Search up to 2 weeks back (handles long holiday runs)
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)

    raise RuntimeError(
        f"Could not find a trading day within 14 days of {reference}. "
        "Check docs/holidays.json."
    )


def reload_holidays() -> None:
    """Clear the holiday cache so changes to holidays.json take effect."""
    _load_holidays.cache_clear()
    log.info("holidays_cache_cleared")
