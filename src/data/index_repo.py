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


async def sync_nifty_constituents(session: AsyncSession) -> int:
    """Fetch the official NSE Nifty 50 constituents CSV and upsert into the DB.

    Downloads from ``settings.nifty50_csv_url``, parses the "Symbol" column,
    appends ".NS", and upserts into the symbols table.  Falls back to
    ``NIFTY50_FALLBACK`` silently if the HTTP request fails.

    Returns:
        Number of symbols upserted/confirmed.
    """
    from datetime import datetime, timezone

    import asyncio

    from src.infra.config import settings

    symbols_to_upsert: list[SymbolInfo] = []

    try:
        raw_csv = await asyncio.to_thread(_fetch_csv_sync, settings.nifty50_csv_url)
        symbols_to_upsert = _parse_nifty_csv(raw_csv)
        log.info("nifty50_csv_fetched", count=len(symbols_to_upsert))
    except Exception as exc:
        log.warning(
            "nifty50_csv_fetch_failed_using_fallback",
            error=str(exc),
            fallback_count=len(NIFTY50_FALLBACK),
        )
        symbols_to_upsert = NIFTY50_FALLBACK

    if not symbols_to_upsert:
        symbols_to_upsert = NIFTY50_FALLBACK

    # Upsert: add if missing, mark existing as active; deactivate removed tickers
    from sqlalchemy import select as _select

    live_tickers = {s.ticker for s in symbols_to_upsert}
    now = datetime.now(timezone.utc)

    result = await session.execute(_select(Symbol))
    existing_symbols: dict[str, Symbol] = {s.ticker: s for s in result.scalars().all()}

    for info in symbols_to_upsert:
        sym = existing_symbols.get(info.ticker)
        if sym is None:
            sym = Symbol(
                ticker=info.ticker,
                company_name=info.company_name,
                is_active=True,
                added_at=now,
                updated_at=now,
            )
            session.add(sym)
        else:
            sym.is_active = True
            sym.updated_at = now

    # Deactivate tickers no longer in the index
    for ticker, sym in existing_symbols.items():
        if ticker not in live_tickers and sym.is_active:
            sym.is_active = False
            sym.updated_at = now

    await session.flush()
    log.info("nifty50_constituents_synced", count=len(symbols_to_upsert))
    return len(symbols_to_upsert)


def _fetch_csv_sync(url: str) -> str:
    """Synchronous HTTP GET for the NSE CSV (called via asyncio.to_thread)."""
    import urllib.request

    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        raw: bytes = resp.read()
    return raw.decode("utf-8", errors="replace")


def _parse_nifty_csv(csv_text: str) -> list[SymbolInfo]:
    """Parse the NSE Nifty 50 CSV and return a list of SymbolInfo.

    The CSV has a "Symbol" column and a "Company Name" column.
    yfinance uses uppercase ticker + ".NS" suffix.
    """
    import csv
    import io

    reader = csv.DictReader(io.StringIO(csv_text))
    results: list[SymbolInfo] = []
    for row in reader:
        symbol = (row.get("Symbol") or "").strip().upper()
        name = (row.get("Company Name") or symbol).strip()
        if symbol:
            results.append(SymbolInfo(ticker=f"{symbol}.NS", company_name=name))
    return results or NIFTY50_FALLBACK
