"""Unit tests for NSE Trading Calendar."""

from datetime import date, datetime
import pytest
from src.data.calendar import NSECalendar


def test_is_weekend():
    calendar = NSECalendar()
    # Saturday
    assert calendar.is_weekend(date(2025, 1, 25)) is True
    # Sunday
    assert calendar.is_weekend(date(2025, 1, 26)) is True
    # Monday
    assert calendar.is_weekend(date(2025, 1, 27)) is False


def test_is_holiday():
    calendar = NSECalendar()
    # 2025-08-15 is Independence Day in holidays.json
    assert calendar.is_holiday(date(2025, 8, 15)) is True
    # Normal trading day
    assert calendar.is_holiday(date(2025, 8, 14)) is False


def test_is_trading_day():
    calendar = NSECalendar()
    # Independence Day 2025 (Friday) -> Not a trading day
    assert calendar.is_trading_day(date(2025, 8, 15)) is False
    # Saturday -> Not a trading day
    assert calendar.is_trading_day(date(2025, 8, 16)) is False
    # Regular Monday -> Trading day
    assert calendar.is_trading_day(date(2025, 8, 18)) is True
