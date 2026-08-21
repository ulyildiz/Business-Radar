# -*- coding: utf-8 -*-
"""Output layer: two CSV files + NOTES.txt + the terminal summary.

Single responsibility: writing to files and to the screen. Calls no APIs and
transforms no data — it only turns finished `Candidate` records into rows.
"""

from __future__ import annotations

import csv
import os
from typing import Any, Dict, Sequence, Tuple

from .console import log, wrap
from .models import (
    FOUND_VIA_LANGSEARCH,
    FOUND_VIA_OSM,
    FOUND_VIA_TOMTOM,
    VERIFY_LANGSEARCH,
    VERIFY_NOT_CHECKED,
    Candidate,
    RunResult,
)

# --- Schemas (FR-12 / FR-13) -------------------------------------------------
NO_WEBSITE_FIELDS = [
    "name", "type", "address", "phone", "distance_m", "sources", "verified", "osm_link",
]
HAS_WEBSITE_FIELDS = [
    "name", "type", "address", "phone", "distance_m", "website", "found_via", "sources",
]
# Diagnostic columns added by --full-columns (omitted by default).
EXTRA_FIELDS = [
    "tomtom_checked", "langsearch_checked", "langsearch_skipped", "email", "opening_hours",
    "lat", "lon", "osm_id", "osm_tag", "tomtom_id", "tomtom_category",
    "social", "notes",
]

COVERAGE_NOTE = (
    "This list covers businesses present in OpenStreetMap and TomTom. "
    "Businesses recorded in neither platform (recently opened, never "
    "registered on any map, running purely on local word of mouth) are "
    "INVISIBLE to this scan. That is a structural limit of free data "
    "sources, not a defect."
)


def output_paths(base: str) -> Tuple[str, str, str]:
    """Derive the three output paths from the base name (FR-12)."""
    root, ext = os.path.splitext(base)
    for suffix in ("_no_website", "_has_website"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
    ext = ext or ".csv"
    return f"{root}_no_website{ext}", f"{root}_has_website{ext}", f"{root}_NOTES.txt"


def _row_of(candidate: Candidate) -> Dict[str, Any]:
    """Every possible column for a record; the writer picks the ones it needs."""
    return {
        "name": candidate.name,
        "type": candidate.biz_type,
        "address": candidate.best_address(),
        "phone": candidate.best_phone(),
        "distance_m": candidate.distance_m,
        "sources": "+".join(candidate.sources),
        "verified": candidate.verification_state(),
        "osm_link": candidate.osm_link,
        "website": candidate.website,
        "found_via": candidate.found_via,
        "tomtom_checked": str(candidate.tomtom_checked).lower(),
        "langsearch_checked": str(candidate.langsearch_checked).lower(),
        "langsearch_skipped": str(candidate.langsearch_skipped).lower(),
        "email": candidate.email,
        "opening_hours": candidate.opening_hours,
        "lat": f"{candidate.lat:.6f}",
        "lon": f"{candidate.lon:.6f}",
        "osm_id": f"{candidate.osm_type}/{candidate.osm_id}" if candidate.osm_id else "",
        "osm_tag": candidate.osm_tags_matched,
        "tomtom_id": candidate.tomtom_id,
        "tomtom_category": candidate.tomtom_category,
        "social": candidate.social,
        "notes": candidate.notes,
    }


def write_csv(path: str, rows: Sequence[Candidate], fields: Sequence[str]) -> None:
    """Write a CSV containing ONLY a header row and data rows (FR-14).

    The coverage note is deliberately kept out of here: a free-text line breaks
    CSV parsers and Excel's column alignment, which corrupts the file that is
    meant to be delivered.
    """
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    # utf-8-sig so Excel opens non-ASCII business names correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for candidate in rows:
            writer.writerow(_row_of(candidate))
    log(f"Written: {path}  ({len(rows)} rows)", level="ok")


def write_notes(path: str, result: RunResult, no_website_path: str, has_website_path: str) -> None:
    """Write the coverage note and this run's counts to a separate file (FR-14)."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("COVERAGE NOTE\n")
        fh.write("=" * 70 + "\n\n")
        for line in wrap(COVERAGE_NOTE, 70):
            fh.write(line + "\n")
        fh.write("\n" + "-" * 70 + "\n")
        fh.write("OUTPUTS OF THIS RUN\n")
        fh.write("-" * 70 + "\n\n")
        fh.write(f"  Center: {result.center.label}\n")
        fh.write(f"  {result.scanned} businesses scanned.\n")
        fh.write(f"  {len(result.no_website)} have NO website  -> {os.path.basename(no_website_path)}\n")
        fh.write(f"  {len(result.has_website)} DO have a website -> {os.path.basename(has_website_path)}\n\n")
        fh.write("The `sources` column shows where the record was ACTUALLY found:\n")
        fh.write("  osm         -> found only in OpenStreetMap\n")
        fh.write("  tomtom      -> found only in TomTom (no OSM record)\n")
        fh.write("  osm+tomtom  -> present in both sources (most reliable records)\n\n")
        fh.write("The `verified` column shows whether the lead passed through Layer 3:\n")
        fh.write(f"  {VERIFY_LANGSEARCH:15s} -> web search ran, no domain of its own was found\n")
        fh.write(f"  {VERIFY_NOT_CHECKED:15s} -> not verified: the layer was disabled, the\n")
        fh.write("                     daily quota ran out, or the request failed. The\n")
        fh.write("                     record stays a lead, but with weaker evidence.\n\n")
        fh.write("The `found_via` column shows WHICH layer found the website:\n")
        fh.write(f"  {FOUND_VIA_OSM:15s} -> OSM website / contact:website tag\n")
        fh.write(f"  {FOUND_VIA_TOMTOM:15s} -> TomTom poi.url field\n")
        fh.write(f"  {FOUND_VIA_LANGSEARCH:15s} -> LangSearch web search\n")
    log(f"Written: {path}", level="ok")


def write_results(result: RunResult, *, base: str, write_has_website: bool,
                  full_columns: bool) -> Tuple[str, str, str]:
    """Write all three outputs and return their paths."""
    no_web_path, has_web_path, notes_path = output_paths(base)
    extra = list(EXTRA_FIELDS) if full_columns else []

    result.no_website.sort(key=lambda c: c.distance_m)
    result.has_website.sort(key=lambda c: (c.found_via, c.distance_m))

    write_csv(no_web_path, result.no_website, NO_WEBSITE_FIELDS + extra)
    if write_has_website:
        write_csv(has_web_path, result.has_website, HAS_WEBSITE_FIELDS + extra)
    write_notes(notes_path, result, no_web_path, has_web_path)
    return no_web_path, has_web_path, notes_path


# ---------------------------------------------------------------------------
# Terminal summary
# ---------------------------------------------------------------------------

def _source_breakdown(records: Sequence[Candidate]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        key = "+".join(record.sources) if record.sources else "osm"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _print_lead_quality(leads: Sequence[Candidate]) -> None:
    """How USABLE the lead list is: sources, verification, contact details."""
    breakdown = _source_breakdown(leads)
    print("Lead sources           : "
          + (", ".join(f"{k}={v}" for k, v in sorted(breakdown.items())) or "-"))
    only_tomtom = sum(1 for c in leads if c.sources == ["tomtom"])
    if only_tomtom:
        print(f"   -> {only_tomtom} leads found ONLY through TomTom (absent from OSM)")

    verified = sum(1 for c in leads if c.langsearch_checked)
    unverified = len(leads) - verified
    print(f"Layer 3 verification   : {verified}/{len(leads)} leads verified"
          + (f", {unverified} marked '{VERIFY_NOT_CHECKED}'" if unverified else ""))

    with_phone = sum(1 for c in leads if c.best_phone())
    with_address = sum(1 for c in leads if c.best_address())
    pct = (100 * with_phone / len(leads)) if leads else 0
    print(f"Leads with a phone     : {with_phone}/{len(leads)}  ({pct:.0f}%)")
    print(f"Leads with an address  : {with_address}/{len(leads)}")


def print_summary(result: RunResult, *, no_web_path: str, has_web_path: str,
                  notes_path: str, wrote_has_website: bool, radius_m: int,
                  type_count: int) -> None:
    """Run summary (FR-15) plus a repeat of the coverage note (FR-14)."""
    leads = result.no_website
    print()
    print("=" * 70)
    print(f"SUMMARY  ({result.elapsed_s:.1f}s)")
    print("=" * 70)
    print(f"Center                 : {result.center.label}")
    print(f"Radius / type count    : {radius_m} m / {type_count}")
    print(f"TomTom mode            : {result.tomtom_mode_used}")
    print()
    print(f"NO WEBSITE (leads)     : {len(leads)}   -> {no_web_path}")
    print(f"HAS WEBSITE            : {len(result.has_website)}   -> "
          f"{has_web_path if wrote_has_website else '(not written)'}")
    for via in (FOUND_VIA_OSM, FOUND_VIA_TOMTOM, FOUND_VIA_LANGSEARCH):
        count = sum(1 for c in result.has_website if c.found_via == via)
        if count:
            print(f"   - {via:22s}: {count}")

    print()
    _print_lead_quality(leads)
    if result.tomtom_request_limit:
        print(f"TomTom requests used   : {result.tomtom_requests_used}/{result.tomtom_request_limit}")
    print("Requests used          : " + ", ".join(
        f"{k}={v}" for k, v in sorted(result.request_counts.items())))

    print("-" * 70)
    print("COVERAGE NOTE:")
    for line in wrap(COVERAGE_NOTE, 68):
        print("  " + line)
    print(f"  (also written to: {notes_path})")
    print("-" * 70)
    print(f"{result.scanned} businesses scanned: {len(leads)} without a website, "
          f"{len(result.has_website)} with one.")
    print("=" * 70)
