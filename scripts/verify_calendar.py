"""Verify and auto-update calendar events using SerpAPI search.
Run: python scripts/verify_calendar.py [--auto-update]
Environment: SERPAPI_KEY required
"""
import argparse
import json
import os
import pathlib
import sys
from datetime import date, timedelta

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
CALENDAR_PATH = ROOT / "data" / "calendar.json"
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

# Event search templates: map category to search query pattern
SEARCH_TEMPLATES = {
    "memory_truth": "{name} {date}",
    "capex_guidance": "{name} earnings capex guidance {date}",
    "optics_earnings": "{name} earnings {date}",
    "trendforce_release": "TrendForce DRAM NAND price {date}",
    "other": "{name} {date}",
}


def serpapi_search(query: str, api_key: str) -> dict:
    """Search via SerpAPI Google endpoint."""
    url = "https://serpapi.com/search"
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": 5,
        "hl": "zh-CN",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def check_event_occurred(event: dict, api_key: str) -> dict:
    """Search web to verify if event occurred. Returns result dict."""
    name = event["name"]
    evt_date = event["date"]
    category = event.get("category", "other")
    
    template = SEARCH_TEMPLATES.get(category, SEARCH_TEMPLATES["other"])
    query = template.format(name=name, date=evt_date)
    
    print(f"[verify] Searching: {query}")
    
    try:
        result = serpapi_search(query, api_key)
        
        # Check if any news results exist
        news_results = result.get("news_results", [])
        organic_results = result.get("organic_results", [])
        
        all_results = news_results + organic_results
        
        if not all_results:
            return {
                "found": False,
                "query": query,
                "message": "No search results found",
                "snippets": [],
            }
        
        # Collect top snippets
        snippets = []
        for r in all_results[:3]:
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            if title or snippet:
                snippets.append(f"{title}: {snippet}")
        
        # Simple heuristic: if results exist, likely occurred
        return {
            "found": True,
            "query": query,
            "message": f"Found {len(all_results)} results",
            "snippets": snippets,
        }
        
    except Exception as e:
        return {
            "found": False,
            "query": query,
            "message": f"Search error: {e}",
            "snippets": [],
        }


def update_calendar(event_name: str, snippets: list) -> bool:
    """Update calendar.json: mark event as confirmed and append result summary."""
    try:
        cal = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
        events = cal.get("events", [])
        
        for evt in events:
            if evt["name"] == event_name:
                # Mark as confirmed
                evt["date_confirmed"] = True
                
                # Append result to hypothesis
                result_summary = " | ".join(snippets[:2])  # Top 2 snippets
                if "实际结果：" not in evt.get("hypothesis", ""):
                    evt["hypothesis"] += f" 实际结果：{result_summary[:200]}"
                
                # Update note
                evt["note"] = f"已验证（{date.today().isoformat()}）- {evt.get('note', '')}"
                
                # Write back
                CALENDAR_PATH.write_text(
                    json.dumps(cal, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                print(f"[verify] UPDATED calendar.json: {event_name} -> confirmed")
                return True
        
        print(f"[verify] WARNING: Event '{event_name}' not found in calendar.json")
        return False
        
    except Exception as e:
        print(f"[verify] ERROR updating calendar: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Verify calendar events")
    parser.add_argument("--auto-update", action="store_true", 
                        help="Automatically update calendar.json when events are found")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be updated without making changes")
    args = parser.parse_args()
    
    if not SERPAPI_KEY:
        print("[verify] ERROR: SERPAPI_KEY not set")
        print("[verify] Set environment variable: export SERPAPI_KEY=your_key")
        sys.exit(1)
    
    if not CALENDAR_PATH.exists():
        print("[verify] ERROR: calendar.json not found")
        sys.exit(1)
    
    cal = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    events = cal.get("events", [])
    
    today = date.today()
    window_start = today - timedelta(days=1)
    window_end = today + timedelta(days=3)
    
    # Find events within window that are not confirmed
    to_verify = []
    for evt in events:
        evt_date = date.fromisoformat(evt["date"])
        if window_start <= evt_date <= window_end and not evt.get("date_confirmed", False):
            to_verify.append(evt)
    
    if not to_verify:
        print(f"[verify] No events to verify in window {window_start} ~ {window_end}")
        sys.exit(0)
    
    print(f"[verify] Found {len(to_verify)} events to verify")
    if args.auto_update:
        print("[verify] AUTO-UPDATE mode enabled")
    if args.dry_run:
        print("[verify] DRY-RUN mode: no changes will be made")
    
    # Results for each event
    results = []
    for evt in to_verify:
        print(f"\n[verify] --- {evt['name']} ({evt['date']}) ---")
        result = check_event_occurred(evt, SERPAPI_KEY)
        results.append({
            "event": evt,
            "result": result,
        })
        print(f"[verify] Result: {result['message']}")
        for snippet in result["snippets"]:
            print(f"[verify]   - {snippet[:120]}...")
        
        # Auto-update if found and enabled
        if result["found"] and args.auto_update and not args.dry_run:
            update_calendar(evt["name"], result["snippets"])
    
    # Print summary
    print(f"\n[verify] === SUMMARY ===")
    for r in results:
        evt = r["event"]
        res = r["result"]
        status = "FOUND" if res["found"] else "NOT FOUND"
        auto_updated = " [AUTO-UPDATED]" if (res["found"] and args.auto_update and not args.dry_run) else ""
        print(f"[verify] {status}{auto_updated} | {evt['date']} | {evt['name']}")
    
    # Exit code: 0 if all found, 1 if any not found (for CI blocking)
    all_found = all(r["result"]["found"] for r in results)
    sys.exit(0 if all_found else 1)


if __name__ == "__main__":
    main()
