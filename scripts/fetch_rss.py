"""
fetch_rss.py

Pulls supplementary items from a small set of confirmed-working RSS feeds
from CBRN-relevant international bodies and NGOs. This is a secondary
signal source -- GDELT (scripts/fetch_gdelt.py) is the backbone.

Feed URLs were verified working (HTTP 200, valid RSS/Atom) as of 2026-09-08.
RSS endpoints move; if a feed starts silently returning zero items, verify
its URL still resolves before assuming there's no news.

Usage:
    python scripts/fetch_rss.py [--out data/raw_rss.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser

# name -> feed URL. Kept short and high-confidence rather than exhaustive.
# DTRA public affairs was investigated but has no discoverable, reliably
# filtered RSS feed as of 2026-09-08 (see README "Open items").
FEEDS: dict[str, str] = {
    "IAEA": "https://www.iaea.org/feeds/topnews",
    "OPCW": "https://www.opcw.org/rss.xml",
    "NTI": "https://www.nti.org/feed/",
}

# Headquarters country of the publishing organization -- used as source_country
# metadata since these feeds don't carry per-article country. This describes
# where the org is based, not where an event occurred.
FEED_HQ_COUNTRY: dict[str, str] = {
    "IAEA": "Austria",
    "OPCW": "Netherlands",
    "NTI": "United States",
}

REQUEST_TIMEOUT = 20


def _parse_entry_date(entry: Any) -> str:
    for field in ("published_parsed", "updated_parsed"):
        struct = getattr(entry, field, None)
        if struct:
            try:
                return datetime(*struct[:6], tzinfo=timezone.utc).isoformat()
            except (TypeError, ValueError):
                continue
    return datetime.now(timezone.utc).isoformat()


def _fetch_one_feed(name: str, url: str) -> list[dict[str, Any]]:
    try:
        parsed = feedparser.parse(url, request_headers={"User-Agent": "cbrne-osint-dashboard/1.0"})
    except Exception as exc:  # feedparser rarely raises, but be defensive
        print(f"[fetch_rss] WARN: failed to parse feed '{name}': {exc}", file=sys.stderr)
        return []

    if parsed.bozo and not parsed.entries:
        print(f"[fetch_rss] WARN: feed '{name}' returned no parseable entries ({parsed.bozo_exception})", file=sys.stderr)
        return []

    if not parsed.entries:
        print(f"[fetch_rss] INFO: feed '{name}' returned zero entries", file=sys.stderr)
        return []

    items = []
    for entry in parsed.entries:
        items.append(
            {
                "title": (entry.get("title") or "").strip(),
                "url": entry.get("link", ""),
                "summary": (entry.get("summary") or "").strip()[:500],
                "source_domain": name,
                "source_country": FEED_HQ_COUNTRY.get(name, ""),
                "tone": None,
                "themes": [],
                "published_date": _parse_entry_date(entry),
                "source": "rss",
                "rss_feed_name": name,
            }
        )
    return items


def fetch_rss_items() -> list[dict[str, Any]]:
    all_items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for name, url in FEEDS.items():
        for item in _fetch_one_feed(name, url):
            if item["url"] and item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                all_items.append(item)

    return all_items


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch supplementary CBRN-relevant RSS items")
    parser.add_argument("--out", type=str, default="data/raw_rss.json", help="Output JSON path")
    args = parser.parse_args()

    items = fetch_rss_items()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[fetch_rss] Wrote {len(items)} deduplicated items to {out_path}")


if __name__ == "__main__":
    main()
