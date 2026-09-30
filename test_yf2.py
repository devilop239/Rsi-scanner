import sys
import asyncio
from datetime import date, timedelta
import pandas as pd
import yfinance as yf

async def run():
    today = date(2026, 9, 30)
    start = today - timedelta(days=10)
    end = today
    
    end_exclusive = end + timedelta(days=1)
    
    tickers = ["ADANIENT.NS", "ADANIPORTS.NS"]
    
    with open("test_output.txt", "w") as f:
        f.write("Testing yf.download with list...\n")
        try:
            df = yf.download(
                tickers=tickers,
                start=start.strftime("%Y-%m-%d"),
                end=end_exclusive.strftime("%Y-%m-%d"),
                auto_adjust=False,
                progress=False,
                threads=False
            )
            f.write(f"Columns: {df.columns}\n")
            f.write(f"Empty: {df.empty}\n")
            
            f.write("Testing xs...\n")
            if isinstance(df.columns, pd.MultiIndex):
                if tickers[0] in df.columns.get_level_values(1):
                    df_sub = df.xs(tickers[0], level=1, axis=1)
                    f.write(f"Sub columns: {df_sub.columns}\n")
        except Exception as e:
            f.write(f"Error: {e}\n")

if __name__ == "__main__":
    asyncio.run(run())
