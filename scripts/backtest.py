import argparse
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from src.domain.indicators import classify_signal, compute_stoch_rsi

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest StochRSI strategy on given tickers.")
    parser.add_argument("--tickers", nargs="+", required=True, help="List of tickers (e.g. INFY.NS TCS.NS)")
    parser.add_argument("--days", type=int, default=365, help="Number of days to look back")
    parser.add_argument("--win-days", type=int, default=3, help="Days ahead to check for win (price higher)")
    return parser.parse_args()

def backtest_ticker(ticker: str, df: pd.DataFrame, win_days: int) -> dict[str, str | int]:
    if df.empty or len(df) < 100:
        return {"Ticker": ticker, "Total Signals": 0, "Win Rate": "N/A", "Max Drawdown": "N/A"}

    # yfinance columns are capitalized (Close)
    close_col = "Close" if "Close" in df.columns else "adj_close"
    if close_col not in df.columns:
        return {"Ticker": ticker, "Total Signals": 0, "Win Rate": "N/A", "Max Drawdown": "N/A"}
        
    try:
        stoch = compute_stoch_rsi(df[close_col])
    except Exception:
        return {"Ticker": ticker, "Total Signals": 0, "Win Rate": "N/A", "Max Drawdown": "N/A"}

    signals = []
    
    k = stoch.k
    prev_k = k.shift(1)
    
    for i in range(1, len(df)):
        pk = prev_k.iloc[i]
        ck = k.iloc[i]
        sig = classify_signal(k=ck, d=None, prev_k=pk, prev_d=None)
        if sig == "OVERSOLD":
            signals.append(i)

    wins = 0
    eval_signals = len(signals)
    
    for idx in signals:
        if idx + win_days < len(df):
            entry_price = float(df[close_col].iloc[idx])
            exit_price = float(df[close_col].iloc[idx + win_days])
            if exit_price > entry_price:
                wins += 1
        else:
            eval_signals -= 1 # Can't evaluate because we are at the end of the series

    win_rate = f"{(wins / eval_signals * 100):.2f}%" if eval_signals > 0 else "0.00%"
    
    # Calculate Max Drawdown
    roll_max = df[close_col].cummax()
    drawdown = df[close_col] / roll_max - 1.0
    max_drawdown = float(drawdown.min())
    
    return {
        "Ticker": ticker,
        "Total Signals": len(signals),
        "Win Rate": win_rate,
        "Max Drawdown": f"{(max_drawdown * 100):.2f}%"
    }

def main() -> None:
    args = parse_args()
    end_date = date.today()
    start_date = end_date - timedelta(days=args.days)
    
    print(f"Fetching data for {args.tickers} from {start_date} to {end_date}...")
    
    try:
        data = yf.download(args.tickers, start=start_date, end=end_date, progress=False)
    except Exception as e:
        print(f"Error fetching data: {e}")
        return

    results = []
    
    if len(args.tickers) == 1:
        ticker = args.tickers[0]
        df = data.copy()
        if isinstance(df.columns, pd.MultiIndex):
            # In case yfinance returns MultiIndex even for single ticker
            if ticker in df.columns.get_level_values(1):
                df = df.xs(ticker, level=1, axis=1)
        results.append(backtest_ticker(ticker, df, args.win_days))
    else:
        for ticker in args.tickers:
            if isinstance(data.columns, pd.MultiIndex) and ticker in data.columns.get_level_values(1):
                df = data.xs(ticker, level=1, axis=1).copy()
                results.append(backtest_ticker(ticker, df, args.win_days))
            else:
                results.append({"Ticker": ticker, "Total Signals": 0, "Win Rate": "Error", "Max Drawdown": "Error"})
                
    results_df = pd.DataFrame(results)
    print("\n--- Backtest Results ---")
    print(results_df.to_string(index=False))

if __name__ == "__main__":
    main()
