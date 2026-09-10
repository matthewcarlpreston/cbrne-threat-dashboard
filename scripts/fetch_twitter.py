"""
fetch_twitter.py

Queries the X (Twitter) API v2 recent tweet-COUNTS endpoint for CBRN-E /
novel-weapons keyword volume, per category, and returns AGGREGATE COUNTS ONLY.

Uses `/2/tweets/counts/recent`, not `/2/tweets/search/recent`. That's a
deliberate correction, not the original design: the search endpoint's
`meta.result_count` is "how many posts came back on this page" -- capped at
`max_results` -- not "how many posts matched". At a low, cost-controlled
max_results (10) every category saturated the cap on every run (result_count
was 10/10/10/10/10/10, run after run), which made the number meaningless as
a cross-category signal. The counts endpoint returns the real match volume
(`meta.total_tweet_count`) with no such cap, is priced per-request rather
than per-post ($0.005/request vs. $0.005/post -- see Cost note below), and
-- as a bonus -- never returns a tweet object at all, so there is no tweet
text in the response to discard in the first place.

Hard constraint (do not relax without re-reading X's Developer Agreement):
this module must never persist or display raw tweet text, handles,
usernames, or user IDs. The counts endpoint makes that structural rather
than a discipline this code has to maintain: its response is just
{start, end, tweet_count} buckets, never post content. That keeps this
source in the "aggregate analysis that retains no personal identifiers"
lane X's terms permit, not the "display requirements" lane (attribution, no
alteration, no iframe embedding) that applies to showing actual X content --
see README.md "X/Twitter" for the citations.

This module also must never be extended to infer a user's political
affiliation, religion, health, or other protected characteristic from post
content -- that is barred by X's terms regardless of stated use case, and
this pipeline has no legitimate use for it anyway (it counts keyword
matches, it doesn't profile authors, and the counts endpoint couldn't
support that even if asked to).

Cost note: X's API has no free tier as of Feb 2026. The counts endpoint is
$0.005/request (flat, regardless of the count returned), separate from the
$0.005/post-read line item the search endpoint would have billed -- see
docs.x.com/x-api/getting-started/pricing. At 7 categories x 4 runs/day
that's ~$0.14/day (~$4/month), well under the ~$1.20/day estimate that
assumed the search endpoint. If TWITTER_BEARER_TOKEN is not set, this script
no-ops (prints one INFO line and returns an empty list) so the rest of the
pipeline is unaffected -- the token is optional infrastructure, not a hard
dependency.

Usage:
    python scripts/fetch_twitter.py [--hours 24] [--out data/raw_twitter.json]

Can also be imported and called as fetch_twitter_signal(hours=24).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

COUNTS_ENDPOINT = "https://api.x.com/2/tweets/counts/recent"
REQUEST_TIMEOUT = 20
SECONDS_BETWEEN_REQUESTS = 1.5

# Bump this whenever the counting method changes in a way that makes old
# twitter_signal rows non-comparable to new ones (e.g. this file's switch
# from search/recent's capped result_count to counts/recent's real
# total_tweet_count). build_json.py drops rows from a different metric
# version on merge instead of averaging incompatible numbers together.
METRIC_VERSION = "counts_recent_v1"

# Short, cost-aware keyword queries per taxonomy category. -is:retweet avoids
# double-counting retweets of the same post; lang:en keeps this consistent
# with the "sourcelang:eng" choice already made for GDELT. Mirrors the split
# in scripts/classify.py: nuclear and radiological are separate categories.
CATEGORY_QUERIES: dict[str, str] = {
    "chemical": '(sarin OR "nerve agent" OR "chemical weapon" OR "chemical attack" OR novichok) -is:retweet lang:en',
    "biological": '(bioweapon OR "biological weapon" OR anthrax OR bioterror) -is:retweet -band -music lang:en',
    "nuclear": '("nuclear weapon" OR "nuclear proliferation" OR "nuclear test" OR IAEA) -is:retweet lang:en',
    "radiological": '("dirty bomb" OR "radiological device" OR "radioactive material" OR "radiation leak") -is:retweet lang:en',
    "explosives": '(IED OR "improvised explosive device" OR "bomb plot" OR "explosives seizure") -is:retweet lang:en',
    "autonomous_weapons": '("autonomous weapon" OR "drone swarm" OR "loitering munition" OR "killer robot") -is:retweet lang:en',
    "directed_energy": '("directed energy weapon" OR "laser weapon" OR "microwave weapon") -is:retweet lang:en',
}


def _get_bearer_token() -> str | None:
    return os.environ.get("TWITTER_BEARER_TOKEN") or None


def _fetch_one_category(category: str, query: str, bearer_token: str, hours: int) -> dict[str, Any] | None:
    headers = {"Authorization": f"Bearer {bearer_token}"}
    start_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "query": query,
        "granularity": "day",
        "start_time": start_time,
    }
    try:
        resp = requests.get(COUNTS_ENDPOINT, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        print(f"[fetch_twitter] WARN: request failed for '{category}': {exc}", file=sys.stderr)
        return None

    if resp.status_code == 429:
        print(f"[fetch_twitter] WARN: rate limited on '{category}', skipping", file=sys.stderr)
        return None
    if resp.status_code in (401, 403):
        print(
            f"[fetch_twitter] WARN: auth/permission error ({resp.status_code}) on '{category}' -- "
            "check TWITTER_BEARER_TOKEN and the key's access tier (counts/recent requires the same "
            "tier as search/recent)",
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

    # This response never contains a tweet object -- just {start, end,
    # tweet_count} buckets. total_tweet_count is the real match volume over
    # the requested window, uncapped.
    result_count = payload.get("meta", {}).get("total_tweet_count", 0)

    return {
        "category": category,
        "result_count": result_count,
        "window_hours": hours,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "twitter",
        "metric": METRIC_VERSION,
    }


def fetch_twitter_signal(hours: int = 24) -> list[dict[str, Any]]:
    """Return one aggregate-count row per category, or [] if no token is configured."""
    bearer_token = _get_bearer_token()
    if not bearer_token:
        print("[fetch_twitter] INFO: TWITTER_BEARER_TOKEN not set, skipping X/Twitter signal", file=sys.stderr)
        return []

    rows = []
    for i, (category, query) in enumerate(CATEGORY_QUERIES.items()):
        if i > 0:
            time.sleep(SECONDS_BETWEEN_REQUESTS)
        row = _fetch_one_category(category, query, bearer_token, hours)
        if row is not None:
            rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch aggregate CBRN-E keyword volume from X/Twitter (counts only)")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours (default: 24)")
    parser.add_argument("--out", type=str, default="data/raw_twitter.json", help="Output JSON path")
    args = parser.parse_args()

    rows = fetch_twitter_signal(hours=args.hours)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[fetch_twitter] Wrote {len(rows)} category rows (counts only) to {out_path}")


if __name__ == "__main__":
    main()
