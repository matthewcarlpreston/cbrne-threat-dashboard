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


def _apply_retention(items: list[dict[str, Any]], retention_days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept = []
    for item in items:
        dt = _parse_iso(item.get("published_date", ""))
        # Keep items with an unparseable date rather than silently losing them.
        if dt is None or dt >= cutoff:
            kept.append(item)
    return kept


def _load_existing_items() -> list[dict[str, Any]]:
    if not LATEST_PATH.exists():
        return []
    try:
        payload = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
        return payload.get("items", [])
    except (json.JSONDecodeError, OSError):
        return []


def build(hours: int, retention_days: int) -> dict[str, Any]:
    print(f"[build_json] Fetching GDELT items (lookback {hours}h)...", file=sys.stderr)
    gdelt_items = fetch_gdelt_items(hours=hours)
    print(f"[build_json] Got {len(gdelt_items)} GDELT items", file=sys.stderr)

    print("[build_json] Fetching RSS items...", file=sys.stderr)
    rss_items = fetch_rss_items()
    print(f"[build_json] Got {len(rss_items)} RSS items", file=sys.stderr)

    new_items = gdelt_items + rss_items
    classified_new = annotate_items(new_items, drop_unmatched=True)
    print(f"[build_json] {len(classified_new)} / {len(new_items)} new items matched the taxonomy", file=sys.stderr)

    existing_items = _load_existing_items()
    merged = _dedupe_by_url(classified_new + existing_items)
    retained = _apply_retention(merged, retention_days)
    retained.sort(key=lambda i: i.get("published_date", ""), reverse=True)

    category_counts: dict[str, int] = {cat: 0 for cat in CATEGORY_LABELS}
    country_counts: dict[str, int] = {}
    for item in retained:
        for cat in item.get("categories", []):
            category_counts[cat] = category_counts.get(cat, 0) + 1
        country = item.get("source_country") or "Unknown"
        if country:
            country_counts[country] = country_counts.get(country, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "retention_days": retention_days,
        "lookback_hours": hours,
        "total_items": len(retained),
        "category_labels": CATEGORY_LABELS,
        "category_counts": category_counts,
        "country_counts": country_counts,
        "items": retained,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build data/latest.json for the CBRN-E OSINT dashboard")
    parser.add_argument("--hours", type=int, default=24, help="GDELT lookback window in hours")
    parser.add_argument("--retention-days", type=int, default=14, help="How many days of items to retain")
    args = parser.parse_args()

    result = build(hours=args.hours, retention_days=args.retention_days)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[build_json] Wrote {result['total_items']} items to {LATEST_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
