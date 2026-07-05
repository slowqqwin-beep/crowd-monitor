"""Shared config loader — single source of truth for ticker parsing.
Both fetch_prices.py and build_panels.py import from here.
"""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

def load_baskets():
    """Returns (benchmark_ticker, baskets_dict, all_tickers_list)."""
    cfg = json.loads((ROOT / "config" / "baskets.json").read_text("utf-8"))
    benchmark = cfg["benchmark"]["ticker"]
    baskets = cfg["baskets"]
    tickers = [benchmark]
    for b in baskets.values():
        tickers.extend(b["tickers"])
    return benchmark, tickers, baskets, cfg
