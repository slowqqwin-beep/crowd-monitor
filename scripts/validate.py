"""Validate data integrity before committing. Fails on any negative test → blocks pipeline.
Run: python validate.py (exit 0 = pass, exit 1 = block)
"""
import json, hashlib, pathlib, sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
SITE_DIR = ROOT / "docs"
BASKETS_PATH = ROOT / "config" / "baskets.json"
CALENDAR_PATH = ROOT / "data" / "calendar.json"

errors = []

# ── N1: Check panel data has no NaN-filled rows ──
pd = DATA_DIR / "panel_data.json"
if pd.exists():
    data = json.loads(pd.read_text(encoding="utf-8"))
    for name, ratio in data.get("ratios", {}).items():
        for d, v in ratio.items():
            if v is None:
                errors.append(f"N1: {name} ratio None at {d}")

# ── N3: Basket changelog integrity ──
baskets = json.loads(BASKETS_PATH.read_text(encoding="utf-8"))
if len(baskets.get("changelog", [])) == 0:
    errors.append("N3: changelog empty — must have at least initial entry")

# ── N4: Price anomaly check ──
N4_EXEMPT_TICKERS = {
    "NBIS": "2025-09-09 +49.4% — confirmed real market event, not data error"
}
PRICES_DIR = ROOT / "data" / "prices"

# ── N10: Ticker set equality — loaded data must match config exactly ──
from loader import load_baskets
_, expected_tickers, _, _ = load_baskets()
actual_tickers = {fp.stem for fp in PRICES_DIR.glob("*.parquet")}
extras = actual_tickers - set(expected_tickers)
missing = set(expected_tickers) - actual_tickers
if extras:
    errors.append(f"N10: extra tickers in data/prices/ (zombie files?): {sorted(extras)}")
if missing:
    errors.append(f"N10: missing tickers: {sorted(missing)} — re-run fetch_prices.py")
# N11: zombie file detection (tickers in prices/ not in config)
if extras:
    errors.append(f"N11: ZOMBIE FILES in data/prices/ — not declared in baskets.json: {sorted(extras)}. Delete them.")
import pandas as pd
import numpy as np
for fp in PRICES_DIR.glob("*.parquet"):
    df = pd.read_parquet(fp)
    if (df["price"] <= 0).any():
        errors.append(f"N4: {fp.stem} has zero/negative price")
    rets = df["price"].pct_change().dropna()
    if fp.stem in N4_EXEMPT_TICKERS:
        continue  # human-confirmed outlier, not a data error
    if (abs(rets) > 0.40).any():
        errors.append(f"N4: {fp.stem} has >±40% daily return")

# ── N5: calendar.json hypothesis required ──
if CALENDAR_PATH.exists():
    cal = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    for evt in cal.get("events", []):
        if not evt.get("hypothesis", "").strip():
            errors.append(f"N5: event '{evt.get('name', '?')}' missing hypothesis")

    # ── N9: Duplicate name collision detection ──
    # name-only UID means identical names produce duplicate ICS UIDs — silently dropped
    names = [evt.get("name", "") for evt in cal.get("events", [])]
    seen = set()
    for n in names:
        if n in seen:
            errors.append(f"N9: duplicate event name '{n}' — periodic events need period suffix (e.g. 2026Q2)")
        seen.add(n)

# ── N2: Panel 3 type validation (runs if trendforce_vintage.json exists, silent skip otherwise) ──
_tf = ROOT / "data" / "trendforce_vintage.json"
if not _tf.exists():
    print("[N2] trendforce_vintage.json not found — Phase 2 check skipped")
else:
    tf = json.loads(_tf.read_text(encoding="utf-8"))
    VALID_TYPES = {"forecast_initial", "forecast_revised", "realized"}
    for rec in tf.get("records", []):
        t = rec.get("type", "")
        if t not in VALID_TYPES:
            errors.append(f"N2: invalid type '{t}' in trendforce_vintage.json — must be forecast_initial|forecast_revised|realized")
    # Check no forecast mixed into realized sequence (per quarter)
    by_quarter = {}
    for rec in tf.get("records", []):
        q = rec.get("quarter", "")
        by_quarter.setdefault(q, []).append(rec.get("type"))
    for q, types in by_quarter.items():
        has_realized = "realized" in types
        has_forecast = any(t.startswith("forecast") for t in types if t)
        if has_realized and has_forecast:
            errors.append(f"N2: quarter {q} has both forecast and realized — must not mix (§6)")

# ── N6: Header positioning statement check ──
header = SITE_DIR / "index.html"
if header.exists():
    html = header.read_text(encoding="utf-8")
    required = "本看板为人眼研究监视器"
    if required not in html:
        errors.append("N6: index.html missing positioning statement")

# ── N7: No derived fields on disk ──
for fp in DATA_DIR.glob("*.json"):
    if fp.name == "panel_data.json":
        data = json.loads(fp.read_text(encoding="utf-8"))
        # Check no revision_gap or countdown in serialized data
        for r in data.get("readings", []):
            if "revision_gap" in r:
                errors.append(f"N7: revision_gap found in readings for {r['basket']}")
        for k in data:
            if "countdown" in k.lower():
                errors.append(f"N7: countdown field in panel_data: {k}")

# ── N8: ICS hypothesis penetration + UID idempotence ──
ics_path = SITE_DIR / "crowd_calendar.ics"
if ics_path.exists():
    ics = ics_path.read_text(encoding="utf-8")
    if "验证假设：" not in ics:
        errors.append("N8: ICS missing hypothesis prefix in DESCRIPTION")
    # Check UID for one event: rescheduling should not change UID
    if CALENDAR_PATH.exists():
        cal = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
        for evt in cal.get("events", []):
            expected_uid = hashlib.sha1(evt["name"].encode()).hexdigest() + "@crowd-monitor"
            if expected_uid not in ics:
                errors.append(f"N8: UID mismatch for '{evt['name']}' — hash may include date")

if errors:
    print("[VALIDATE] FAILED:")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)
else:
    print("[VALIDATE] PASSED")
    sys.exit(0)
