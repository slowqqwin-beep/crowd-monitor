"""Fetch daily prices from yfinance for all basket tickers + benchmark.
Output: data/prices/{ticker}.parquet
"""
"""Fetch daily prices from yfinance for all basket tickers + benchmark.
Output: data/prices/{ticker}.parquet
"""
import pathlib
import yfinance as yf
import pandas as pd
from loader import load_baskets

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRICES_DIR = ROOT / "data" / "prices"
PRICES_DIR.mkdir(parents=True, exist_ok=True)

benchmark, all_tickers, baskets, cfg = load_baskets()

print(f"[fetch] {len(all_tickers)} tickers: {sorted(all_tickers)}")

for ticker in sorted(all_tickers):
    try:
        t = yf.Ticker(ticker)
        df = t.history(period="1y")
        if df.empty:
            print(f"  [WARN] {ticker}: empty")
            continue
        # Keep only Adj Close, normalize to daily
        prices = df[["Close"]].rename(columns={"Close": "price"})
        prices.index = prices.index.tz_localize(None).normalize()
        out = PRICES_DIR / f"{ticker}.parquet"
        prices.to_parquet(out)
        print(f"  {ticker}: {len(prices)} rows -> {out.name}")
    except Exception as e:
        print(f"  [ERR] {ticker}: {e}")

print("[fetch] Done.")
