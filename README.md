# CBRN-E & Novel Weapons OSINT Dashboard

A static, automatically-refreshing dashboard that aggregates and classifies **publicly
reported news coverage** of CBRN-E (Chemical, Biological, Radiological/Nuclear, Explosives)
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

Once deployed to GitHub Pages, the dashboard is available at:
`https://<your-username>.github.io/<repo-name>/`

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

**X/Twitter**: evaluated and intentionally excluded. X discontinued free API read access
in Feb 2026; there's no working free tier for new access, and this project didn't spend
money on API access for a portfolio piece.

## Classification

Six categories, MVP implementation as keyword/regex matching against each item's
headline (`scripts/classify.py`):

- Chemical
- Biological
- Radiological/Nuclear
- Explosives
- Autonomous Weapons
- Directed Energy Weapons

An item can match multiple categories. Items matching **zero** categories are dropped
before publishing — this is what keeps GDELT's broad `theme:WMD` net from flooding the
dashboard with unrelated news. A couple of vocabulary collisions (e.g. the band
"Anthrax") are handled with small negative-context guards; see the comments in
`classify.py`.

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

## Attribution

- [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)
- [IAEA](https://www.iaea.org/), [OPCW](https://www.opcw.org/), [NTI](https://www.nti.org/) news feeds
- [Chart.js](https://www.chartjs.org/)
