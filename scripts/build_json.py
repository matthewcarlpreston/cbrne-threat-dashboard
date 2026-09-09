"""
build_json.py

Orchestrates the pipeline: fetch GDELT + RSS -> classify -> merge -> dedupe
-> apply retention window -> write data/latest.json for the static
dashboard to consume.

This is the single entry point the GitHub Actions workflow calls.

Usage:
    python scripts/build_json.py [--hours 24] [--retention-days 14]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from classify import annotate_items, CATEGORY_LABELS  # noqa: E402
from fetch_gdelt import fetch_gdelt_items  # noqa: E402
from fetch_rss import fetch_rss_items  # noqa: E402
from fetch_twitter import fetch_twitter_signal, MIN_RESULTS as TWITTER_MIN_RESULTS  # noqa: E402

DATA_DIR = Path(__file__).parent.parent / "data"
LATEST_PATH = DATA_DIR / "latest.json"


def _dedupe_by_url(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for item in items:
        url = item.get("url")
        if url and url not in seen:
            seen.add(url)
            out.append(item)
    return out


def _parse_iso(dt_str: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _apply_retention(
    items: list[dict[str, Any]], retention_days: int, date_field: str = "published_date"
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept = []
    for item in items:
        dt = _parse_iso(item.get(date_field, ""))
        # Keep items with an unparseable date rather than silently losing them.
        if dt is None or dt >= cutoff:
            kept.append(item)
    return kept


def _load_existing() -> dict[str, Any]:
    if not LATEST_PATH.exists():
        return {}
    try:
        return json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def build(hours: int, retention_days: int, twitter_max_results: int) -> dict[str, Any]:
    print(f"[build_json] Fetching GDELT items (lookback {hours}h)...", file=sys.stderr)
    gdelt_items = fetch_gdelt_items(hours=hours)
    print(f"[build_json] Got {len(gdelt_items)} GDELT items", file=sys.stderr)

    print("[build_json] Fetching RSS items...", file=sys.stderr)
    rss_items = fetch_rss_items()
    print(f"[build_json] Got {len(rss_items)} RSS items", file=sys.stderr)

    new_items = gdelt_items + rss_items
    classified_new = annotate_items(new_items, drop_unmatched=True)
    print(f"[build_json] {len(classified_new)} / {len(new_items)} new items matched the taxonomy", file=sys.stderr)

    print("[build_json] Fetching X/Twitter signal (aggregate counts only)...", file=sys.stderr)
    twitter_rows = fetch_twitter_signal(max_results=twitter_max_results)
    print(f"[build_json] Got {len(twitter_rows)} X/Twitter category rows", file=sys.stderr)

    existing = _load_existing()
    existing_items = existing.get("items", [])
    existing_signal = existing.get("twitter_signal", [])

    merged = _dedupe_by_url(classified_new + existing_items)
    retained = _apply_retention(merged, retention_days)
    retained.sort(key=lambda i: i.get("published_date", ""), reverse=True)

    merged_signal = twitter_rows + existing_signal
    retained_signal = _apply_retention(merged_signal, retention_days, date_field="fetched_at")
    retained_signal.sort(key=lambda r: r.get("fetched_at", ""), reverse=True)

    category_counts: dict[str, int] = {cat: 0 for cat in CATEGORY_LABELS}
    country_counts: dict[str, int] = {}
    for item in retained:
        for cat in item.get("categories", []):
            category_counts[cat] = category_counts.get(cat, 0) + 1
        country = item.get("source_country") or "Unknown"
        if country:
            country_counts[country] = country_counts.get(country, 0) + 1

    # Sum of aggregate X result_counts per category over the retained window --
    # a volume proxy, not an item list. No tweet content/handles/IDs ever
    # reach this structure; see scripts/fetch_twitter.py.
    twitter_category_totals: dict[str, int] = {cat: 0 for cat in CATEGORY_LABELS}
    for row in retained_signal:
        cat = row.get("category")
        if cat in twitter_category_totals:
            twitter_category_totals[cat] += row.get("result_count", 0)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "retention_days": retention_days,
        "lookback_hours": hours,
        "total_items": len(retained),
        "category_labels": CATEGORY_LABELS,
        "category_counts": category_counts,
        "country_counts": country_counts,
        "items": retained,
        "twitter_signal": retained_signal,
        "twitter_category_totals": twitter_category_totals,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build data/latest.json for the CBRN-E OSINT dashboard")
    parser.add_argument("--hours", type=int, default=24, help="GDELT lookback window in hours")
    parser.add_argument("--retention-days", type=int, default=14, help="How many days of items to retain")
    parser.add_argument(
        "--twitter-max-results", type=int, default=TWITTER_MIN_RESULTS,
        help="X/Twitter results read per category per run (each is a billed read -- keep low)",
    )
    args = parser.parse_args()

    result = build(hours=args.hours, retention_days=args.retention_days, twitter_max_results=args.twitter_max_results)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[build_json] Wrote {result['total_items']} items to {LATEST_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
