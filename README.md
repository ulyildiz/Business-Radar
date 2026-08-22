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
      └─► TomTom (discover)   ─┘         │
                                         ▼
                               has a website?  ← single decision point
                                 │            │
                                NO           YES
                                 │            │
                                 ▼            ▼
                     _no_website.csv    _has_website.csv
```

Two design points worth knowing:

**TomTom is a peer discovery source, not a verifier.** In the first version it could only eliminate records, so a business with no OSM entry never entered the pipeline at all — only about 8% of results carried a phone number. TomTom now runs its own grid sweep and **adds** what it finds. The `sources` column records where each business was actually found.

**The website filter runs AFTER the merge.** Otherwise a business whose website tag exists in only one source could slip into the lead list through its duplicate from the other source. A record counts as having a site only when a real domain of its own is present — social-media and directory links do not count, so a business running on Instagram alone stays a lead.

---

## One run at a time

Rate limits and daily quotas are scoped to the **API key**, not to the process.
Two runs started together each throttle themselves correctly and still present
double the load to the server, which produces sustained 429s from the very
first request while each run honestly reports zero usage of its own. Measured:
two processes launched in the same second did exactly this.

Per-process throttling cannot solve that, so runs are serialized with a lock
file. A second run is refused:

```
[x] Another businessfind run is already using this API key.
    lock held by PID 25916, running for 1s (.businessfind.lock)
```

The lock is released on exit and reclaimed automatically if a previous run was
killed. `--no-run-lock` overrides it — only safe with a **different** API key.

Within a run, requests are paced **before** they are sent rather than after a
429 comes back: a rejected request has already been spent. On a 429 the
interval for that host grows persistently (+0.3 s, up to +3 s) and creeps back
down after a long clean streak, because the real limit lives on the server and
shifts over time. Retries use exponential backoff with jitter — a fixed delay
just makes every waiting request return at the same instant and trip the limit
again.

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
| `osm_link` | Link to the OSM record |

### `<base>_has_website.csv` — businesses that have a site

`name, type, address, phone, distance_m, website, found_via, sources`

`found_via`: `osm_tag` · `tomtom_poi_url`

### `<base>_NOTES.txt`

The coverage note and a column glossary. **No explanatory line is ever written inside the CSVs** — free text breaks CSV parsers and Excel's column alignment, which corrupts the file you are about to deliver.

`--full-columns` adds diagnostic columns (`notes`, `lat/lon`, `tomtom_checked`, …). Files are written as `utf-8-sig` so Excel opens non-ASCII business names correctly.

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

**429s keep happening, and the attempt counter always shows `(1/3)`.**
That pattern means each line is the *first* attempt of a *separate* request, so
every new request is being rejected immediately — not one request retrying.
Check for a second run first: two terminals started at once, or `--no-run-lock`
passed by mistake. Concurrent runs on one key are the usual cause. Otherwise
raise `--delay-tomtom`.

**"Another businessfind run is already using this API key" (exit code 3).**
A run is in progress. Wait for it, or pass `--no-run-lock` if you are genuinely
using a different key. If a previous run was killed, the stale lock is detected
and reclaimed automatically.

**"API key invalid or unauthorized (HTTP 401)".**
Check `TOMTOM_API_KEY` in your `.env`, or run with `--skip-tomtom`.

**The TomTom daily quota runs out mid-scan.**
The scan continues and the CSVs are still written; the remaining area is simply
not swept by TomTom. `--dry-run` estimates the cost beforehand — raise
`--tomtom-cell-radius` or lower `--radius` to fit within it.

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
| `filelock.py` | Cross-process lock (single-run guard, safe quota updates) |
| `config.py` | `.env` loading merged with CLI settings (writes no files) |
| `http_client.py` | Rate limiting, QPM window, backoff, retries |
| `geocode.py` | Nominatim |
| `osm_source.py` | Overpass query and parsing |
| `tomtom_source.py` | TomTom discovery / verification |
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
