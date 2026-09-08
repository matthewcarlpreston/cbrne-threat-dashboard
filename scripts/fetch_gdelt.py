"""
fetch_gdelt.py

Queries the GDELT DOC 2.0 API (https://api.gdeltproject.org/api/v2/doc/doc)
for publicly reported news coverage relevant to CBRN-E and novel-weapons
proliferation, incidents, seizures, and policy actions.

This script retrieves ONLY article metadata that GDELT itself already
surfaces from open news coverage (headline, source URL, source country,
tone score, matched GKG themes, publish date). It does not fetch, store,
or process article body text, and it performs no keyword expansion beyond
the public-safety-relevant search terms below. See README.md "Scope
guardrail".

Usage:
    python scripts/fetch_gdelt.py [--hours 24] [--out data/raw_gdelt.json]

Can also be imported and called as fetch_gdelt_items(hours=24).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

# Each entry: (query_tag, GDELT query string).
# query_tag is just provenance metadata carried through to classify.py /
# build_json.py -- it is NOT the final category tag (classify.py re-derives
# categories from title text so multi-domain stories get all matching tags).
QUERIES: list[tuple[str, str]] = [
    ("wmd_theme", "theme:WMD"),
    (
        "explosives_keyword",
        '("improvised explosive device" OR IED OR "explosives seizure" OR '
        '"bomb plot" OR "weapons cache" OR "explosive precursor")',
    ),
    (
        "autonomous_weapon_keyword",
        '("autonomous weapon" OR "drone swarm" OR "loitering munition" OR '
        '"killer robot" OR "lethal autonomous")',
    ),
    (
        "directed_energy_keyword",
        '("directed energy weapon" OR "laser weapon system" OR '
        '"microwave weapon" OR "high power microwave")',
    ),
]

MAX_RECORDS = 250
REQUEST_TIMEOUT = 30
SECONDS_BETWEEN_REQUESTS = 6.0  # GDELT asks for >=1 request per 5s from a given IP
USER_AGENT = "cbrne-osint-dashboard/1.0 (+https://github.com/; portfolio project)"


def _build_url(query: str, hours: int) -> str:
    params = {
        "query": f"{query} sourcelang:eng",
        "mode": "artlist",
        "maxrecords": str(MAX_RECORDS),
        "timespan": f"{hours}h",
        "sort": "datedesc",
        "format": "json",
    }
    return f"{GDELT_ENDPOINT}?{urllib.parse.urlencode(params)}"


def _parse_gdelt_date(raw: str) -> str:
    """GDELT 'seendate' looks like 20260908T153000Z -> ISO 8601."""
    try:
        dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return raw


def _fetch_one_query(query_tag: str, query: str, hours: int, retries: int = 2) -> list[dict[str, Any]]:
    url = _build_url(query, hours)
    resp = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        except requests.RequestException as exc:
            print(f"[fetch_gdelt] WARN: request failed for '{query_tag}': {exc}", file=sys.stderr)
            return []

        if resp.status_code == 429 and attempt < retries:
            backoff = SECONDS_BETWEEN_REQUESTS * (attempt + 2)
            print(
                f"[fetch_gdelt] WARN: rate limited on '{query_tag}', retrying in {backoff:.0f}s "
                f"(attempt {attempt + 1}/{retries})",
                file=sys.stderr,
            )
            time.sleep(backoff)
            continue
        break

    if resp.status_code == 429:
        print(f"[fetch_gdelt] WARN: rate limited on '{query_tag}', giving up for this run", file=sys.stderr)
        return []
    if resp.status_code != 200:
        print(
            f"[fetch_gdelt] WARN: HTTP {resp.status_code} for '{query_tag}': {resp.text[:200]}",
            file=sys.stderr,
        )
        return []

    text = resp.text.strip()
    if not text:
        print(f"[fetch_gdelt] INFO: empty response body for '{query_tag}'", file=sys.stderr)
        return []

    try:
        payload = resp.json()
    except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
        print(f"[fetch_gdelt] WARN: non-JSON response for '{query_tag}', skipping", file=sys.stderr)
        return []

    articles = payload.get("articles", [])
    if not articles:
        print(f"[fetch_gdelt] INFO: no results for '{query_tag}'", file=sys.stderr)
        return []

    items = []
    for art in articles:
        items.append(
            {
                "title": (art.get("title") or "").strip(),
                "url": art.get("url", ""),
                "source_domain": art.get("domain", ""),
                "source_country": art.get("sourcecountry", "") or "Unknown",
                "tone": _safe_float(art.get("tone")),
                "themes": [],  # DOC 2.0 artlist mode doesn't return GKG themes per-article
                "published_date": _parse_gdelt_date(art.get("seendate", "")),
                "language": art.get("language", ""),
                "image_url": art.get("socialimage", ""),
                "source": "gdelt",
                "gdelt_query_tag": query_tag,
            }
        )
    return items


def _safe_float(val: Any) -> float | None:
    try:
        return round(float(val), 3)
    except (TypeError, ValueError):
        return None


def fetch_gdelt_items(hours: int = 24) -> list[dict[str, Any]]:
    """Run all configured GDELT queries and return a deduplicated item list."""
    all_items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for i, (query_tag, query) in enumerate(QUERIES):
        if i > 0:
            time.sleep(SECONDS_BETWEEN_REQUESTS)
        items = _fetch_one_query(query_tag, query, hours)
        for item in items:
            if item["url"] and item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                all_items.append(item)

    return all_items


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch CBRN-E related articles from GDELT DOC 2.0 API")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours (default: 24)")
    parser.add_argument("--out", type=str, default="data/raw_gdelt.json", help="Output JSON path")
    args = parser.parse_args()

    items = fetch_gdelt_items(hours=args.hours)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[fetch_gdelt] Wrote {len(items)} deduplicated items to {out_path}")


if __name__ == "__main__":
    main()
