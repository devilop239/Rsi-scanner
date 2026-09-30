"""Nifty 50 index constituents repository.

Sources (in priority order):
  1. symbols table in the database (live source of truth after first sync)
  2. Bundled fallback list (used when DB is empty or on first run)

The NSE CSV fetch is optional and done via a management command, not at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.logging import get_logger
from src.infra.models import Symbol

log = get_logger(__name__)


@dataclass(frozen=True)
class SymbolInfo:
    ticker: str        # yfinance format, e.g. "RELIANCE.NS"
    company_name: str


# ---------------------------------------------------------------------------
# Bundled fallback — Nifty 50 constituents as of Oct 2024
# ---------------------------------------------------------------------------
NIFTY50_FALLBACK: list[SymbolInfo] = [
    SymbolInfo("ADANIENT.NS", "Adani Enterprises Ltd"),
    SymbolInfo("ADANIPORTS.NS", "Adani Ports & SEZ Ltd"),
    SymbolInfo("APOLLOHOSP.NS", "Apollo Hospitals Enterprise Ltd"),
    SymbolInfo("ASIANPAINT.NS", "Asian Paints Ltd"),
    SymbolInfo("AXISBANK.NS", "Axis Bank Ltd"),
    SymbolInfo("BAJAJ-AUTO.NS", "Bajaj Auto Ltd"),
    SymbolInfo("BAJAJFINSV.NS", "Bajaj Finserv Ltd"),
    SymbolInfo("BAJFINANCE.NS", "Bajaj Finance Ltd"),
    SymbolInfo("BHARTIARTL.NS", "Bharti Airtel Ltd"),
    SymbolInfo("BPCL.NS", "Bharat Petroleum Corporation Ltd"),
    SymbolInfo("BRITANNIA.NS", "Britannia Industries Ltd"),
    SymbolInfo("CIPLA.NS", "Cipla Ltd"),
    SymbolInfo("COALINDIA.NS", "Coal India Ltd"),
    SymbolInfo("DIVISLAB.NS", "Divi's Laboratories Ltd"),
    SymbolInfo("DRREDDY.NS", "Dr Reddy's Laboratories Ltd"),
    SymbolInfo("EICHERMOT.NS", "Eicher Motors Ltd"),
    SymbolInfo("GRASIM.NS", "Grasim Industries Ltd"),
    SymbolInfo("HCLTECH.NS", "HCL Technologies Ltd"),
    SymbolInfo("HDFCBANK.NS", "HDFC Bank Ltd"),
    SymbolInfo("HDFCLIFE.NS", "HDFC Life Insurance Company Ltd"),
    SymbolInfo("HEROMOTOCO.NS", "Hero MotoCorp Ltd"),
    SymbolInfo("HINDALCO.NS", "Hindalco Industries Ltd"),
    SymbolInfo("HINDUNILVR.NS", "Hindustan Unilever Ltd"),
    SymbolInfo("ICICIBANK.NS", "ICICI Bank Ltd"),
    SymbolInfo("INDUSINDBK.NS", "IndusInd Bank Ltd"),
    SymbolInfo("INFY.NS", "Infosys Ltd"),
    SymbolInfo("ITC.NS", "ITC Ltd"),
    SymbolInfo("JSWSTEEL.NS", "JSW Steel Ltd"),
    SymbolInfo("KOTAKBANK.NS", "Kotak Mahindra Bank Ltd"),
    SymbolInfo("LT.NS", "Larsen & Toubro Ltd"),
    SymbolInfo("M&M.NS", "Mahindra & Mahindra Ltd"),
    SymbolInfo("MARUTI.NS", "Maruti Suzuki India Ltd"),
    SymbolInfo("NESTLEIND.NS", "Nestle India Ltd"),
    SymbolInfo("NTPC.NS", "NTPC Ltd"),
    SymbolInfo("ONGC.NS", "Oil & Natural Gas Corporation Ltd"),
    SymbolInfo("POWERGRID.NS", "Power Grid Corporation of India Ltd"),
    SymbolInfo("RELIANCE.NS", "Reliance Industries Ltd"),
    SymbolInfo("SBILIFE.NS", "SBI Life Insurance Company Ltd"),
    SymbolInfo("SBIN.NS", "State Bank of India"),
    SymbolInfo("SHRIRAMFIN.NS", "Shriram Finance Ltd"),
    SymbolInfo("SUNPHARMA.NS", "Sun Pharmaceutical Industries Ltd"),
    SymbolInfo("TATACONSUM.NS", "Tata Consumer Products Ltd"),
    SymbolInfo("TATAMOTORS.NS", "Tata Motors Ltd"),
    SymbolInfo("TATASTEEL.NS", "Tata Steel Ltd"),
    SymbolInfo("TCS.NS", "Tata Consultancy Services Ltd"),
    SymbolInfo("TECHM.NS", "Tech Mahindra Ltd"),
    SymbolInfo("TITAN.NS", "Titan Company Ltd"),
    SymbolInfo("ULTRACEMCO.NS", "UltraTech Cement Ltd"),
    SymbolInfo("WIPRO.NS", "Wipro Ltd"),
    SymbolInfo("ZOMATO.NS", "Zomato Ltd"),
]


async def get_active_symbols(session: AsyncSession) -> list[Symbol]:
    """Return all active symbols from the database.

    Falls back to seeding from NIFTY50_FALLBACK if the table is empty.
    """
    result = await session.execute(
        select(Symbol).where(Symbol.is_active == True).order_by(Symbol.ticker)  # noqa: E712
    )
    symbols = list(result.scalars().all())

    if not symbols:
        log.warning("symbols_table_empty_seeding_from_fallback")
        symbols = await _seed_symbols(session)

    return symbols


async def _seed_symbols(session: AsyncSession) -> list[Symbol]:
    """Insert the bundled fallback list into the symbols table."""
    from datetime import datetime, timezone

    new_symbols: list[Symbol] = []
    for info in NIFTY50_FALLBACK:
        now = datetime.now(timezone.utc)
        sym = Symbol(
            ticker=info.ticker,
            company_name=info.company_name,
            is_active=True,
            added_at=now,
            updated_at=now,
        )
        session.add(sym)
        new_symbols.append(sym)

    await session.flush()  # Assigns IDs without committing
    log.info("symbols_seeded", count=len(new_symbols))
    return new_symbols


def get_fallback_tickers() -> list[str]:
    """Return all Nifty 50 ticker strings from the bundled fallback list."""
    return [s.ticker for s in NIFTY50_FALLBACK]
