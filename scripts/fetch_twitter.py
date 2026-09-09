"""
fetch_twitter.py

Queries the X (Twitter) API v2 recent-search endpoint for CBRN-E / novel-weapons
keyword volume, per category, and returns AGGREGATE COUNTS ONLY.

Hard constraint (do not relax without re-reading X's Developer Agreement):
this module must never persist or display raw tweet text, handles, usernames,
or user IDs. The API response necessarily contains tweet text (X always
returns `id` + `text` at minimum), but this script reads only `len(data)`
and `created_at` from each response and discards the rest before it ever
reaches a return value, a log line, or disk. That keeps this source in the
"aggregate analysis that retains no personal identifiers" lane X's terms
permit, not the "display requirements" lane (attribution, no alteration, no
iframe embedding) that applies to showing actual X content -- see
README.md "X/Twitter" for the citations.

This module also must never be extended to infer a user's political
affiliation, religion, health, or other protected characteristic from post
content -- that is barred by X's terms regardless of stated use case, and
this pipeline has no legitimate use for it anyway (it counts keyword
matches, it doesn't profile authors).

Cost note: X's API has no free tier as of Feb 2026 (~$0.005/read). Each
category query reads up to `max_results` posts. Keep `max_results` and the
number of categories queried deliberately low -- this is a low-cost aggregate
signal, not a comprehensive collection. If TWITTER_BEARER_TOKEN is not set,
this script no-ops (prints one INFO line and returns an empty list) so the
rest of the pipeline is unaffected -- the token is optional infrastructure,
not a hard dependency.

Usage:
    python scripts/fetch_twitter.py [--max-results 10] [--out data/raw_twitter.json]

Can also be imported and called as fetch_twitter_signal(max_results=10).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

SEARCH_ENDPOINT = "https://api.x.com/2/tweets/search/recent"
REQUEST_TIMEOUT = 20
SECONDS_BETWEEN_REQUESTS = 1.5
MIN_RESULTS = 10  # X API v2 minimum for max_results on this endpoint

# Short, cost-aware keyword queries per taxonomy category. -is:retweet avoids
# paying for/double-counting retweets of the same post; lang:en keeps this
# consistent with the "sourcelang:eng" choice already made for GDELT.
CATEGORY_QUERIES: dict[str, str] = {
    "chemical": '(sarin OR "nerve agent" OR "chemical weapon" OR "chemical attack" OR novichok) -is:retweet lang:en',
    "biological": '(bioweapon OR "biological weapon" OR anthrax OR bioterror) -is:retweet -band -music lang:en',
    "radiological_nuclear": '("nuclear weapon" OR "dirty bomb" OR "nuclear proliferation" OR IAEA) -is:retweet lang:en',
    "explosives": '(IED OR "improvised explosive device" OR "bomb plot" OR "explosives seizure") -is:retweet lang:en',
    "autonomous_weapons": '("autonomous weapon" OR "drone swarm" OR "loitering munition" OR "killer robot") -is:retweet lang:en',
    "directed_energy": '("directed energy weapon" OR "laser weapon" OR "microwave weapon") -is:retweet lang:en',
}


def _get_bearer_token() -> str | None:
    return os.environ.get("TWITTER_BEARER_TOKEN") or None


def _fetch_one_category(category: str, query: str, bearer_token: str, max_results: int) -> dict[str, Any] | None:
    headers = {"Authorization": f"Bearer {bearer_token}"}
    params = {
        "query": query,
        "max_results": str(max(max_results, MIN_RESULTS)),
        "tweet.fields": "created_at",
    }
    try:
        resp = requests.get(SEARCH_ENDPOINT, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        print(f"[fetch_twitter] WARN: request failed for '{category}': {exc}", file=sys.stderr)
        return None

    if resp.status_code == 429:
        print(f"[fetch_twitter] WARN: rate limited on '{category}', skipping", file=sys.stderr)
        return None
    if resp.status_code in (401, 403):
        print(
            f"[fetch_twitter] WARN: auth/permission error ({resp.status_code}) on '{category}' -- "
            "check TWITTER_BEARER_TOKEN and the key's access tier",
            file=sys.stderr,
        )
        return None
    if resp.status_code != 200:
        print(f"[fetch_twitter] WARN: HTTP {resp.status_code} for '{category}': {resp.text[:200]}", file=sys.stderr)
        return None

    try:
        payload = resp.json()
    except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
        print(f"[fetch_twitter] WARN: non-JSON response for '{category}', skipping", file=sys.stderr)
        return None

    # Extract ONLY count + timestamps. `data` (if present) holds tweet objects
    # with text/ids -- deliberately not read beyond this point, and never
    # assigned to a variable that escapes this function.
    data = payload.get("data", [])
    result_count = payload.get("meta", {}).get("result_count", len(data))
    latest_created_at = None
    if data:
        timestamps = [t.get("created_at") for t in data if t.get("created_at")]
        if timestamps:
            latest_created_at = max(timestamps)
    del data  # explicit: raw tweet objects (with text) go out of scope here, unused

    return {
        "category": category,
        "result_count": result_count,
        "latest_created_at": latest_created_at,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "twitter",
    }


def fetch_twitter_signal(max_results: int = MIN_RESULTS) -> list[dict[str, Any]]:
    """Return one aggregate-count row per category, or [] if no token is configured."""
    bearer_token = _get_bearer_token()
    if not bearer_token:
        print("[fetch_twitter] INFO: TWITTER_BEARER_TOKEN not set, skipping X/Twitter signal", file=sys.stderr)
        return []

    rows = []
    for i, (category, query) in enumerate(CATEGORY_QUERIES.items()):
        if i > 0:
            time.sleep(SECONDS_BETWEEN_REQUESTS)
        row = _fetch_one_category(category, query, bearer_token, max_results)
        if row is not None:
            rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch aggregate CBRN-E keyword volume from X/Twitter (counts only)")
    parser.add_argument(
        "--max-results", type=int, default=MIN_RESULTS,
        help=f"Results per category query (min {MIN_RESULTS}; each result is a billed read -- keep this low)",
    )
    parser.add_argument("--out", type=str, default="data/raw_twitter.json", help="Output JSON path")
    args = parser.parse_args()

    rows = fetch_twitter_signal(max_results=args.max_results)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[fetch_twitter] Wrote {len(rows)} category rows (counts only) to {out_path}")


if __name__ == "__main__":
    main()
