import asyncio
from datetime import date, timedelta
from src.data.yfinance_provider import YFinanceProvider
from src.infra.logging import configure_logging

async def main():
    configure_logging(log_level="DEBUG", json_logs=False)
    provider = YFinanceProvider()
    today = date(2026, 9, 30)
    start = today - timedelta(days=10)
    
    # Try 15 tickers
    tickers = ["ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS"]
    res = await provider.fetch_candles(tickers, start, today)
    print("Keys returned:", res.keys())

if __name__ == "__main__":
    asyncio.run(main())
