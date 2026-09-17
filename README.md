# CBRN-E & Novel Weapons OSINT Dashboard

A static, automatically-refreshing dashboard that aggregates and classifies **publicly
reported news coverage** of CBRN-E (Chemical, Biological, Radiological, Nuclear, Explosives)
and novel-weapons (autonomous systems, directed-energy) events — proliferation, seizures,
incidents, policy actions, and sanctions.

Built as a portfolio project demonstrating data pipeline design, domain classification,
automation, and a deployable analytical product, with the same "public-signal, not
technical-uplift" scoping discipline that threat-intelligence and safeguards work requires.

## Scope guardrail

**This project aggregates and classifies publicly reported news coverage. It does not
collect, generate, or reference any technical weapons information** (synthesis routes,
device design, targeting data, precursor sourcing, etc.). Every item in the dashboard is
a headline + link to an already-public news article or press release — nothing here is
scraped from restricted sources, nothing is inferred beyond what a news outlet already
published, and no full article bodies are stored. This is an open-source-intelligence
**signal-detection** tool, not a weapons reference.

## Live dashboard

**https://matthewcarlpreston.github.io/cbrne-threat-dashboard/**

## Architecture

GitHub Pages only serves static files — it can't run a live backend — so the pipeline
runs on a schedule and commits its output as data:

```
GitHub Actions (cron, every 6 hrs)
        │
        ▼
Python fetch/classify pipeline (scripts/build_json.py)
        │
        ▼
writes data/latest.json ──► commits to repo
        │
        ▼
GitHub Pages (static site) reads data/latest.json via fetch()
        │
        ▼
HTML/CSS/JS dashboard renders it
```

No server to maintain, no hosting cost, and the git commit history of `data/latest.json`
is a free, browsable time-series archive of what the pipeline saw at each run.

## Repo structure

```
cbrne-threat-dashboard/
├── .github/workflows/update-data.yml   # scheduled Action (every 6h + manual dispatch)
├── scripts/
│   ├── fetch_gdelt.py                  # GDELT DOC 2.0 API queries
│   ├── fetch_rss.py                    # IAEA / OPCW / NTI RSS feeds
│   ├── fetch_twitter.py                # X/Twitter aggregate keyword-volume counts (optional)
│   ├── classify.py                     # keyword/regex domain classification
│   └── build_json.py                   # orchestrates fetch → classify → merge → write
├── data/
│   └── latest.json                     # current snapshot the dashboard reads
├── index.html / style.css / app.js     # static dashboard (GitHub Pages source: repo root)
└── README.md
```

## Data sources

**GDELT DOC 2.0 API** (primary, free, no key required)
- Endpoint: `https://api.gdeltproject.org/api/v2/doc/doc`
- Rolling window queried every pipeline run: last 24 hours, sorted by date.
- One query against the GKG `theme:WMD` tag (broad CBRN net), plus three targeted
  keyword queries for explosives/IED, autonomous weapons, and directed-energy weapons —
  GDELT has no dedicated GKG theme for those three domains.
- The API is free and keyless but rate-limits aggressively (observed: ~1 request per 5s
  per IP, with `429` responses under bursty use). `fetch_gdelt.py` waits 6s between its
  four queries and retries with backoff on `429`; a query that still fails is skipped
  for that run rather than failing the whole pipeline.
- GDELT's `theme:WMD` tag is intentionally broad and noisy on its own — roughly 90% of
  what it returns on a given day is unrelated news that happens to share vocabulary
  (courts, entertainment, sports). `classify.py`'s keyword pass is what makes the final
  feed on-topic; see Classification below.

**RSS feeds** (secondary/supplementary — confirmed working 2026-09-08)
| Source | Feed URL |
|---|---|
| IAEA | `https://www.iaea.org/feeds/topnews` |
| OPCW | `https://www.opcw.org/rss.xml` |
| NTI | `https://www.nti.org/feed/` |

DTRA public affairs was investigated but has no discoverable, reliably-filtered public
RSS feed as of this writing (its site platform doesn't expose one, and DVIDS' generic
unit-search feed isn't actually filterable by unit through the URL) — it was left out
rather than wired up to something unreliable. RSS endpoints move over time; if a feed
starts silently returning zero items, verify the URL still resolves before assuming
there's no news (a genuinely empty feed happens too — NTI's has returned zero `<item>`
entries during testing while still being a valid, reachable feed).

**X/Twitter** (optional, aggregate-counts only — `scripts/fetch_twitter.py`)
- X discontinued free API read access in Feb 2026; there is no working free tier, so this
  source is opt-in and requires a `TWITTER_BEARER_TOKEN` (see Secrets below). Without it,
  `fetch_twitter.py` no-ops — one log line, empty result, rest of the pipeline unaffected.
- Uses the v2 **recent tweet-counts endpoint** (`tweets/counts/recent`), not the search
  endpoint, with one short keyword query per taxonomy category (e.g. chemical: `sarin OR
  "nerve agent" OR "chemical weapon" OR "chemical attack" OR novichok`), `-is:retweet lang:en`
  to cut noise/double-counting.
- **This was a deliberate correction, not the original design.** The first version used
  `tweets/search/recent` and read `meta.result_count` as a volume signal. In practice every
  category saturated the requested page size (10/10/10/10/10/10, run after run) because
  `result_count` on that endpoint is "how many posts came back on this page," capped at
  `max_results` — not "how many posts matched." That made every category's number identical
  and meaningless the moment true volume exceeded 10 in a run, which it did for all six.
  Switching to `tweets/counts/recent` fixed it: it returns `meta.total_tweet_count`, the real
  uncapped match volume for the query window, with no page-size ceiling.
- **Deliberately aggregate-only by construction, not just by convention** — and more strongly
  so after the endpoint switch. `tweets/counts/recent` never returns a tweet object at all;
  its response is just `{start, end, tweet_count}` buckets, so there is no tweet text, handle,
  or user ID in the response to discard in the first place (the search endpoint would have
  required discarding it after the fact, which the original version did). This follows the
  same two-provision read of X's Developer Agreement that motivated the design:
  [display requirements](https://docs.x.com/developer-terms/agreement) (attribution, no
  content alteration, no iframe embedding of X content as of the April 2026 agreement) apply
  to *showing* X content, while [aggregate analysis that retains no personal
  identifiers](https://developer.x.com/developer-terms/more-on-restricted-use-cases) is
  explicitly permitted — this pipeline only ever does the latter. Per the same terms, this
  project will never infer a user's political affiliation, religion, health, or other
  protected characteristic from post content; the pipeline has no mechanism to do so (it
  counts keyword matches per category, it does not read or retain author-level data, and the
  counts endpoint couldn't support that even if asked to).
- **Cost**: pay-per-use, no free tier. `tweets/counts/recent` is billed **$0.005 per request,
  flat**, regardless of the count returned — a separate line item from the $0.005/post-read
  the search endpoint would have billed ([source](https://docs.x.com/x-api/getting-started/pricing)).
  At 7 categories on the pipeline's 6-hour cadence that's ~28 requests/day ≈ **$0.14/day**
  (~$4/month) — about a tenth of the original search-endpoint estimate, and it doesn't
  consume the separate per-account post-read cap either. Lower the cron frequency in
  `update-data.yml` or drop the `TWITTER_BEARER_TOKEN` secret entirely to cut spend to zero.
  Confirm your key's actual access tier/billing status in the X developer portal before
  enabling this in a scheduled workflow.
- Dashboard shows this as a single "X/Twitter keyword volume" bar chart — the **most recent**
  reading per category, not a running total. `fetch_twitter.py` asks for a 24h count on every
  run but the workflow runs every 6h, so those windows overlap; summing them would
  quadruple-count the same posts. `data/latest.json` still retains the full `twitter_signal`
  history (one row per category per run, 14-day window) if you want the raw time series.

## Classification

Seven categories, MVP implementation as keyword/regex matching against each item's
headline (`scripts/classify.py`):

- Chemical
- Biological
- Nuclear
- Radiological
- Explosives
- Autonomous Weapons
- Directed Energy Weapons

**Nuclear and Radiological are separate categories, not a combined "Radiological/Nuclear"
bucket.** A nuclear weapon, fissile material, or an IAEA safeguards story is not the same
threat picture as a dirty bomb or a loose/orphan radioactive source, and folding them
together obscured that distinction. Nuclear covers weapons, fuel-cycle material, and the
nonproliferation regime around them (warheads, enriched uranium, IAEA, nuclear
proliferation/tests/smuggling); Radiological covers dispersal devices and
contamination/exposure incidents (dirty bombs, orphan sources, radiation leaks) — see the
comments in `classify.py` for the exact split and reasoning.

An item can match multiple categories. Items matching **zero** categories are dropped
before publishing — this is what keeps GDELT's broad `theme:WMD` net from flooding the
dashboard with unrelated news. A couple of vocabulary collisions (e.g. the band
"Anthrax") are handled with small negative-context guards; see the comments in
`classify.py`.

Existing stored items are **re-classified on every pipeline run**, not just new ones — so
when the taxonomy itself changes (as it did with this split), already-published items pick
up the new categories immediately instead of carrying a stale/unknown category key for up
to `retention_days` until they age out.

This is an intentional starting point. An embeddings- or LLM-based classifier could
replace or augment the keyword pass later without changing the output schema (each
item's `categories` list) — noted here as a natural next step, not implemented for MVP.

Each item also carries: source, URL, headline, publish timestamp, source country (for
GDELT items — actual outlet location; for RSS items — the publishing organization's
headquarters, since these feeds don't carry per-article geography), and tone/sentiment
score (GDELT items only).

## Dashboard

Static HTML/CSS/JS, no build step, reads `data/latest.json` via `fetch()`:
- Category and time-range filters, plus free-text search
- Summary stat tiles (items shown, countries reporting, leading category, retention window)
- Category breakdown bar chart (Chart.js)
- Top reporting countries
- **Coverage trend** — daily item counts per category over the selected time range, as
  small multiples (one sparkline-style chart per category) rather than one combined
  chart. With 7 categories at very different volumes (Nuclear routinely runs 10-100x
  Radiological), overlapping lines in a single chart would be hard to tell apart by
  color alone; a chart per category sidesteps that. Respects the same category and
  time-range filters as the rest of the dashboard.
- X/Twitter keyword-volume chart (current per-category count, aggregate only; shows an
  empty state if the source isn't configured for a given deployment)
- Filterable/searchable item feed, newest first

## Build phases (as executed)

1. **Pipeline first.** Built and manually ran `fetch_gdelt.py` against the live API,
   inspected real output, then built `classify.py` and tuned it against real GDELT noise
   before automating anything.
2. **Automate.** `.github/workflows/update-data.yml` — scheduled cron + commit-back.
3. **Dashboard.** Static site reading the committed JSON, deployable to GitHub Pages.
4. **Polish.** This README, retention/rate-limit handling, dark-mode support.

## Retention & cadence

- `build_json.py` merges each run's new items into the existing `data/latest.json` and
  applies a **14-day retention window** (`--retention-days`, configurable) before writing —
  so short of the git history, the live file itself carries two weeks of signal, not just
  the latest 24-hour pull.
- The GitHub Action runs every 6 hours. GDELT's free API has no published hard quota but
  publishes rate-limit guidance (~1 req/5s/IP) and asks high-volume users to switch to
  their bulk datasets — a 6-hour cadence across 4 queries is well inside reasonable use.

## Secrets

`TWITTER_BEARER_TOKEN` (optional) — only needed to enable the X/Twitter signal.
1. GitHub repo → Settings → Secrets and variables → Actions → New repository secret,
   name it `TWITTER_BEARER_TOKEN`, paste the bearer token value there.
2. `.github/workflows/update-data.yml` passes it to the pipeline as an env var
   (`${{ secrets.TWITTER_BEARER_TOKEN }}`) — never committed, never logged.
3. To test locally, set it as a local environment variable in your own shell before
   running `build_json.py`/`fetch_twitter.py` — don't put it in a file that gets committed.
4. Removing the secret (or never adding it) simply turns this source off; nothing else
   in the pipeline depends on it.

## Running locally

```bash
python -m venv .venv
source .venv/Scripts/activate   # or .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python scripts/build_json.py --hours 24 --retention-days 14
python -m http.server 8080      # serve the static site
```

Then open `http://localhost:8080`.

## Known limitations

- Keyword/regex classification will miss paraphrased coverage and can still admit some
  false positives beyond the ones already guarded against.
- GDELT source-country reflects the reporting outlet's location, not necessarily where an
  event occurred.
- RSS-sourced items carry the publishing organization's HQ country, not per-article
  geography.
- Duplicate coverage of the same story by different outlets is expected and shown as
  separate items (dedup is by exact URL only).
- X/Twitter counts are keyword-match volume, not verified relevance — a post matching
  "nuclear weapon" isn't necessarily *about* nuclear weapons policy the way a classified
  news headline is. Treat the X chart as a rough attention signal, not an equivalent to
  the item feed's precision.
- The X chart shows a point-in-time snapshot (most recent run), not a cumulative total —
  see the X/Twitter data-source notes above for why summing across runs would double-count.

## Attribution

- [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)
- [IAEA](https://www.iaea.org/), [OPCW](https://www.opcw.org/), [NTI](https://www.nti.org/) news feeds
- [X Developer Agreement](https://docs.x.com/developer-terms/agreement) /
  [X restricted use cases](https://developer.x.com/developer-terms/more-on-restricted-use-cases) /
  [X API pricing](https://docs.x.com/x-api/getting-started/pricing)
- [Chart.js](https://www.chartjs.org/)
