"""Build panels: relative strength ratios, z-scores, scissors spread, ICS.
Reads data/prices/*.parquet, writes docs/data/*.json + docs/crowd_calendar.ics
"""
import json, hashlib, pathlib
from datetime import date, datetime, timedelta
import pandas as pd
import numpy as np
from loader import load_baskets

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRICES_DIR = ROOT / "data" / "prices"
CALENDAR_PATH = ROOT / "data" / "calendar.json"
SITE_DIR = ROOT / "docs"
DATA_DIR = SITE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

benchmark_ticker, all_tickers, baskets_cfg, cfg = load_baskets()

# ── Load prices ──

prices = {}
for t in sorted(all_tickers):
    fp = PRICES_DIR / f"{t}.parquet"
    if fp.exists():
        df = pd.read_parquet(fp)
        if not df.empty:
            prices[t] = df["price"]

if not prices:
    raise SystemExit("No price data found. Run fetch_prices.py first.")

# Align dates
all_dates = sorted(set().union(*[set(s.index) for s in prices.values()]))
base_idx = pd.DatetimeIndex(all_dates)
price_df = pd.DataFrame({t: prices[t].reindex(base_idx) for t in prices if t in prices}, index=base_idx)

# ── Basket indices (equal-weight daily returns, base=100) ──
START_DATE = pd.Timestamp("2025-07-01")
price_df = price_df[price_df.index >= START_DATE]

def basket_index(tickers, df):
    valid = [t for t in tickers if t in df.columns]
    if not valid:
        return pd.Series(np.nan, index=df.index)
    rets = df[valid].pct_change().fillna(0.0)
    # Equal weight daily return
    daily = rets.mean(axis=1)
    # Cumulative
    idx = (1 + daily).cumprod() * 100
    return idx

benchmark_idx = basket_index([benchmark_ticker], price_df)
basket_indices = {}
for name, basket in baskets_cfg.items():
    basket_indices[name] = basket_index(basket["tickers"], price_df)

# ── Relative strength ratios vs SOXX ──
ratios = {}
for name, idx in basket_indices.items():
    r = idx / benchmark_idx
    r_drop = r.dropna()
    if len(r_drop) == 0:
        ratios[name] = pd.Series(1.0, index=r.index)
    else:
        ratios[name] = r / r_drop.iloc[0]

# ── Derivative metrics ──
def momentum(series, window):
    return (series / series.shift(window) - 1) * 100

def rolling_zscore(series, window=250):
    if len(series.dropna()) < window:
        return (series - series.dropna().mean()) / series.dropna().std()
    return (series - series.rolling(window).mean()) / series.rolling(window).std()

readings = []
for name, r in ratios.items():
    latest = r.dropna()
    if len(latest) == 0:
        continue
    cur = latest.iloc[-1]
    mom20 = momentum(r, 20).dropna().iloc[-1] if len(r.dropna()) > 20 else None
    mom60 = momentum(r, 60).dropna().iloc[-1] if len(r.dropna()) > 60 else None
    zs = rolling_zscore(r).dropna()
    z = float(zs.iloc[-1]) if len(zs) > 0 else None
    readings.append({
        "basket": name,
        "label": baskets_cfg[name]["label"],
        "ratio": round(float(cur), 4),
        "momentum_20d": round(float(mom20), 2) if mom20 is not None else None,
        "momentum_60d": round(float(mom60), 2) if mom60 is not None else None,
        "z_score": round(float(z), 2) if z is not None else None,
    })

# ── Scissors spread: MEM/SOXX - OPT/SOXX ──
scissors = None
if "MEM" in ratios and "OPT" in ratios:
    scissors = ratios["MEM"] - ratios["OPT"]

# ── Serialize for rendering ──
def serialize_series(series):
    """Convert pd.Series to {date: value} dict for JSON."""
    s = series.dropna()
    return {d.strftime("%Y-%m-%d"): round(float(v), 4) for d, v in s.items()}

panel_data = {
    "generated": date.today().isoformat(),
    "data_as_of": str(price_df.index[-1].date()),
    "stale_days": (date.today() - price_df.index[-1].date()).days,
    "readings": readings,
    "benchmark_ticker": benchmark_ticker,
    "scissors": serialize_series(scissors) if scissors is not None else {},
    "ratios": {name: serialize_series(r) for name, r in ratios.items()},
    "baskets": {name: {"label": b["label"], "tickers": b["tickers"]}
                for name, b in baskets_cfg.items()},
    "config": {"version": cfg["config_version"], "changelog": cfg["changelog"]},
    "config_change_dates": [c["date"] for c in cfg.get("changelog", []) if c["date"] != cfg["changelog"][0]["date"]],
}

with open(DATA_DIR / "panel_data.json", "w", encoding="utf-8") as f:
    json.dump(panel_data, f, ensure_ascii=False, indent=1)

print(f"[build] panel_data.json written, as_of={panel_data['data_as_of']}")

# ── Panel 4: Calendar JSON (pass-through) ──
if CALENDAR_PATH.exists():
    cal = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    with open(DATA_DIR / "calendar.json", "w", encoding="utf-8") as f:
        json.dump(cal, f, ensure_ascii=False, indent=1)

# ── §7.5: ICS generation ──
_CATEGORY_LABELS = {
    "memory_truth": "存储真值",
    "capex_guidance": "Capex指引",
    "trendforce_release": "TrendForce发布",
    "optics_earnings": "光链财报",
    "other": "其他",
}

def make_ics(events):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//crowd-monitor//CROWD//EN",
        "X-WR-CALNAME:CROWD 信息节点",
        "X-WR-TIMEZONE:Asia/Shanghai",
    ]
    for evt in events:
        uid_seed = evt["name"]  # §7.5: hash on name only, not date — reschedule keeps same UID
        uid = hashlib.sha1(uid_seed.encode()).hexdigest() + "@crowd-monitor"

        d_str = evt["date"]
        dt = datetime.strptime(d_str, "%Y-%m-%d")
        dtstart = dt.strftime("%Y%m%d")
        dtend = (dt + timedelta(days=1)).strftime("%Y%m%d")  # RFC 5545 exclusive end

        cat_label = _CATEGORY_LABELS.get(evt.get("category", "other"), "其他")
        confirmed = evt.get("date_confirmed", False)
        prefix = "（预估）" if not confirmed else ""
        summary = f"{prefix}{evt['name']}［{cat_label}］"

        desc_parts = [f"验证假设：{evt['hypothesis']}"]
        if evt.get("note"):
            desc_parts.append(f"备注：{evt['note']}")
        description = "\\n".join(desc_parts)

        lines.extend([
            "BEGIN:VEVENT",
            f"DTSTART;VALUE=DATE:{dtstart}",
            f"DTEND;VALUE=DATE:{dtend}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{description}",
            f"UID:{uid}",
            "BEGIN:VALARM",
            "TRIGGER:-PT24H",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{evt['name']} 倒计时1天",
            "END:VALARM",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

if CALENDAR_PATH.exists():
    cal = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    ics_text = make_ics(cal["events"])
    ics_path = SITE_DIR / "crowd_calendar.ics"
    ics_path.write_text(ics_text, encoding="utf-8")
    print(f"[build] {ics_path.name} written, {len(cal['events'])} events")

print("[build] Done.")
