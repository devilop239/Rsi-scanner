"""Unit and integration tests for ScannerService."""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.data.base import CandleData
from src.infra.models import Symbol
from src.services.scanner import ScannerService, ScanResult


@pytest.fixture
def mock_provider():
    """Mock MarketDataProvider that returns synthetic price series."""
    provider = MagicMock()

    # Generate 150 days of synthetic price data
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=150, freq="D")
    prices = np.linspace(100.0, 200.0, 150)
    df = pd.DataFrame(
        {
            "open": prices * 0.99,
            "high": prices * 1.01,
            "low": prices * 0.98,
            "close": prices,
            "adj_close": prices,
            "volume": 10000,
        },
        index=dates,
    )

    candle_data = CandleData(ticker="RELIANCE.NS", df=df)
    provider.fetch_candles = AsyncMock(return_value={"RELIANCE.NS": candle_data})
    return provider


@pytest.mark.asyncio
async def test_scanner_service_execution(db_session, mock_provider):
    """Test scanner run with seeded symbols and mock market provider."""
    # Seed symbol in DB
    sym = Symbol(ticker="RELIANCE.NS", company_name="Reliance Industries Ltd", is_active=True)
    db_session.add(sym)
    await db_session.commit()

    # Mock DB manager wrapping db_session
    db_manager = MagicMock()
    
    # Create an async context manager for db_manager.session()
    class SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    db_manager.session = MagicMock(return_value=SessionCtx())

    scanner = ScannerService(provider=mock_provider, db=db_manager)
    result = await scanner.run_scan(force=True)

    assert isinstance(result, ScanResult)
    assert result.processed >= 0
    assert result.was_skipped is False
