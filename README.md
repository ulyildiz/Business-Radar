# businessfind

Scans local businesses around a GPS point and splits them into two lists:

- **`_no_website.csv`** — businesses with no website. The lead list for cold outreach.
- **`_has_website.csv`** — businesses that already have one. A separate audience for redesign, SEO, or hosting offers.

It uses only **free APIs that never ask for a credit card**, and has exactly one runtime dependency: `requests`.

---

## Quick start

```bash
make setup
```

This creates a virtual environment, installs dependencies **into that venv**, and copies `.env.example` to `.env` if it does not exist yet. Then fill in `.env`:

```
CONTACT_EMAIL=you@your-agency.com
TOMTOM_API_KEY=<TomTom Developer Portal>
LANGSEARCH_API_KEY=<LangSearch dashboard>
```

Verify the installation without touching the network or any API key:

```bash
make check
```

Run a real scan:

```bash
make run ARGS="--address Kadikoy,Istanbul --radius 2000 --types restaurant hair_salon"
```

`CONTACT_EMAIL` is not optional: the Nominatim and Overpass usage policies require a descriptive User-Agent containing contact details. If a required key is missing, the tool **does not quietly continue with an empty key** — it stops with an error that tells you exactly what to do.

---

## APIs used

| Layer | Service | Role | Free tier | Card? |
|---|---|---|---|---|
| Geocode | **Nominatim** (OSM) | Address → coordinates, coordinates → locality | 1 request/s (policy) | No |
| 1a | **Overpass** (OSM) | Primary discovery — every business in the radius | None (fair use) | No |
| 1b | **TomTom Search** | **Independent** discovery — adds businesses missing from OSM | 2,500 requests/day | No |
| 3 | **LangSearch Web Search** | Verification — "does it really have no site?" | 1,000/day, 60/min, 1/s | No |

Services whose terms forbid storing or exporting results (Yandex Places, Mapbox Search Box) were deliberately excluded — the deliverable here is a CSV file, which those terms do not permit.

---

## How it works

```
address / coordinates
      │
      ├─► Nominatim ─────────────────► center point
      │
      ├─► Overpass (OSM)      ─┐
      │                        ├─► MERGE (name similarity + ≤75 m)
      ├─► TomTom (discover)   ─┘         │
      │                                  ▼
      │                        has a website?  ← single decision point
      │                          │            │
      │                         NO           YES
      │                          │            │
      └─► LangSearch ────────────┤            │
          (verification only)    │            │
              site found ────────┼───────────►┤
                                 ▼            ▼
                     _no_website.csv    _has_website.csv
```

Three design points worth knowing:

**TomTom is a peer discovery source, not a verifier.** In the first version it could only eliminate records, so a business with no OSM entry never entered the pipeline at all — only about 8% of results carried a phone number. TomTom now runs its own grid sweep and **adds** what it finds. The `sources` column records where each business was actually found.

**The website filter runs AFTER the merge.** Otherwise a business whose website tag exists in only one source could slip into the lead list through its duplicate from the other source.

**LangSearch cannot be used for discovery.** It is not a Maps/Places API. It only takes candidates that still look website-less and checks them once more with a general web search.

---

## Layer 3 rate limiting

This section documents the outcome of a debugging session; the behaviour is deliberate.

The LangSearch free tier has **three** limits: 1 per second (QPS), 60 per minute (QPM), and 1,000 per day (QPD). The first implementation targeted only QPS:

> Measurement: 28 requests / 29 seconds = **58 requests per minute**. The QPM ceiling is 60.

So the 1.1-second interval satisfied the per-second limit while running **pinned against the per-minute ceiling**. On short scans retries absorbed the failures; on a 451-candidate run the 429s clustered and the remaining records were passed over unverified. The binding constraint was QPM, not QPS.

Current behaviour:

1. **Rolling 60-second window** (`--langsearch-per-minute`, default 55). An interval alone is not enough: it cannot see the extra requests produced by retries. The window counts actual requests. The default is 55 rather than 60 because the server's minute window does not start at the same instant as ours; running exactly at the ceiling means constantly scraping against it.
2. **Proactive interval** (`--langsearch-delay`, default 1.1 s) — applied *before* each request. Seeing a 429 and only then backing off is a reaction that comes too late.
3. **Adaptive slowdown.** On a 429 the layer's interval grows persistently (+0.3 s, up to +3 s) and creeps back down after a long clean streak. The real limit lives on the server and shifts over time; adapting to measured behaviour is the only correct response.
4. **Exponential backoff with jitter** (2 → 4 → 8 s plus a random margin). Fixed delays are forbidden: requests that all wait the same amount return at the same instant and trip the limit again. *Note: LangSearch does not send a `Retry-After` header (measured). The code supports it, but this is the path that actually runs.*
5. **Daily counter** in `.langsearch_quota_state.json`, reset when the date changes. Several runs on the same day draw from one pool, and a counter kept only in memory cannot see that. Retries are counted too, because the server deducts them as well.
6. **Circuit breaker.** An invalid key (401/403) stops the layer on the **first** candidate — every candidate would give the same answer, so waiting only wastes time and quota. For other persistent failures the threshold is 10 consecutive candidates; a 429 no longer counts as a permanent fault, because the adaptive slowdown recovers on its own.

**451 candidates ≈ 8–9 minutes.** That is not slowness; it is the price of the free tier. The estimate is shown in `--dry-run` and again when the layer starts.

When the quota runs out or the layer stops, **no record is dropped**. It stays in the lead list, marked `not_checked` in the `verified` column.

---

## Outputs

Three files are produced from `-o <base>`:

### `<base>_no_website.csv` — the lead list

| Column | Meaning |
|---|---|
| `name` | Business name |
| `type` | Canonical business type |
| `address` | Address (from OSM or TomTom) |
| `phone` | Phone (from OSM or TomTom) |
| `distance_m` | Distance from the center, in meters |
| `sources` | `osm` / `tomtom` / `osm+tomtom` — where the record was **actually** found |
| `verified` | `langsearch` = searched, no domain of its own found. `not_checked` = could not verify |
| `osm_link` | Link to the OSM record |

### `<base>_has_website.csv` — businesses that have a site

`name, type, address, phone, distance_m, website, found_via, sources`

`found_via`: `osm_tag` · `tomtom_poi_url` · `langsearch`

### `<base>_NOTES.txt`

The coverage note and a column glossary. **No explanatory line is ever written inside the CSVs** — free text breaks CSV parsers and Excel's column alignment, which corrupts the file you are about to deliver.

`--full-columns` adds diagnostic columns (`notes`, `lat/lon`, `langsearch_skipped`, …). Files are written as `utf-8-sig` so Excel opens non-ASCII business names correctly.

### Why the `verified` column exists

`not_checked` does not remove a lead from the list; it says the evidence behind it is weaker. Silently calling such a record "has no website" would be wrong — an unverified record is not the same thing as a verified one. If you run with `--skip-langsearch`, every row is `not_checked`. That is honest reporting, not a failure.

---

## Common commands

```bash
make dry-run ARGS="--address Kadikoy,Istanbul --radius 3000 --types all"
```

Shows the plan, the estimated quota cost, and the expected duration without issuing a single HTTP request. If the quota would be exceeded, it says what to change.

```bash
make list-types
```

59 business types with 101 aliases, so an operator can type the word they actually use for a trade instead of the OSM tag name.

```bash
make probe ARGS="--address Kadikoy,Istanbul --types car_repair"
```

Shows what TomTom returns for each business type. **Run this before your first production scan:** TomTom category search is text based, and if a search text returns an unrelated trade it will not tell you — it just returns those results. Correct a bad match with `--tomtom-category car_repair="oto sanayi"`.

| Target | What it does |
|---|---|
| `make setup` | venv + dependencies + `.env` |
| `make check` | Smoke test with no network and no keys |
| `make run` | Runs a scan (`ARGS=` passes arguments) |
| `make dry-run` | Plan and quota estimate without requests |
| `make probe` | TomTom category sanity check |
| `make list-types` | Supported business types |
| `make freeze` | Writes `requirements.lock.txt` |
| `make clean` | Removes `__pycache__` |
| `make distclean` | Removes the venv (**leaves `.env` and CSVs alone**) |

The Makefile runs under both Git Bash and PowerShell/cmd: paths always use forward slashes, and no shell built-in or redirection is used.

---

## Troubleshooting

**429s keep happening.**
Try `--langsearch-per-minute 40`. Adaptive slowdown is already active; this lowers the persistent ceiling. On a paid tier the opposite applies: for Tier 1 (QPS=5) use `--langsearch-delay 0.25 --langsearch-per-minute 200`.

**"API key rejected (HTTP 401)".**
Check `LANGSEARCH_API_KEY` in your `.env`. The layer stops at the first candidate, but the scan still completes and the CSVs are written.

**Every row says `not_checked`.**
Layer 3 never ran: no key, `--skip-langsearch` was passed, or the daily quota was exhausted. The "Layer 3 verification" line in the terminal summary tells you which.

**The daily quota runs out early.**
`.langsearch_quota_state.json` counts every run on the same day, test runs included. `--langsearch-daily-limit 0` disables tracking (it does not raise the real server-side limit).

**No businesses found.**
Widen the radius or try other business types. OSM coverage is sparse in some areas; make sure you have not passed `--skip-tomtom`.

---

## Module map

Split by single responsibility, with **one-way** dependencies — a lower layer never imports a higher one.

| Module | Responsibility |
|---|---|
| `console.py` | Terminal messages |
| `text_utils.py` | Name normalization, website/domain classification |
| `geo_utils.py` | Haversine, meters↔degrees, grid tiling |
| `models.py` | `Candidate` / `Center` / `RunResult` schemas |
| `quota.py` | Day-scoped request counter persisted to disk |
| `config.py` | `.env` loading merged with CLI settings (writes no files) |
| `http_client.py` | Rate limiting, QPM window, backoff, retries |
| `geocode.py` | Nominatim |
| `osm_source.py` | Overpass query and parsing |
| `tomtom_source.py` | TomTom discovery / verification |
| `langsearch_verify.py` | Layer 3 |
| `merge.py` | Merging and the website split |
| `output.py` | CSV / NOTES / terminal summary |
| `pipeline.py` | **Thin** orchestrator — contains no business logic |
| `cli.py` | Arguments and entry point |

`.env` holds the real keys and is **never committed**; `.env.example` carries the same variable names with no values and is committed.

### A note on language

The interface, logs, and comments are English throughout. Two things stay in the source language on purpose, because they are **data rather than UI text**:

- `TYPE_ALIASES` in `osm_source.py` — input vocabulary, so an operator can type `kuafor` instead of `hair_salon`.
- `GENERIC_BIZ_WORDS` and `NON_WEBSITE_DOMAINS` in `text_utils.py` — the trade words and marketplace domains the matching algorithm needs in order to work in its target market.

Translating these away would remove features rather than internationalize them. The response locale for TomTom is configurable via `--tomtom-language` instead of being hardcoded.

---

## Known limits

This list covers businesses present in OpenStreetMap and TomTom. Businesses recorded in neither platform (recently opened, never registered on any map, running purely on local word of mouth) are **invisible** to this scan. That is a structural limit of free data sources, not a defect.

Domain matching is deliberately **conservative**: a false exclusion is a lost lead, and it happens silently, whereas a false keep costs one wasted outreach call. That is why a generic domain such as `kebapci.com` is not accepted as the website of "Ali Usta Kebap" — a distinctive part of the name has to match.
