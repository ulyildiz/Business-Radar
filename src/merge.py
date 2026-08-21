# -*- coding: utf-8 -*-
"""Merging (deduplicating) OSM + TomTom results and splitting by website.

Single responsibility: combining record sets and making the "does it have a
website" decision in exactly ONE place. Issues no network requests and writes
no files.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .console import log
from .geo_utils import haversine_m, meters_to_deg
from .models import SOURCE_TOMTOM, Candidate
from .text_utils import name_similarity

Bucket = Tuple[int, int]


class _SpatialIndex:
    """Coarse location buckets — avoids comparing every record with every other."""

    def __init__(self, cell_m: float):
        self.cell_m = max(cell_m, 1.0) * 2
        self._buckets: Dict[Bucket, List[Candidate]] = {}

    def _key(self, candidate: Candidate) -> Bucket:
        dlat, dlon = meters_to_deg(candidate.lat, self.cell_m)
        return (int(candidate.lat / dlat), int(candidate.lon / dlon))

    def add(self, candidate: Candidate) -> None:
        self._buckets.setdefault(self._key(candidate), []).append(candidate)

    def neighbours(self, candidate: Candidate) -> List[Candidate]:
        bx, by = self._key(candidate)
        out: List[Candidate] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                out.extend(self._buckets.get((bx + dx, by + dy), []))
        return out


def _find_match(index: _SpatialIndex, tomtom: Candidate,
                max_distance_m: float, name_threshold: float) -> Optional[Candidate]:
    """Find the OSM record that is the same business as this TomTom record."""
    best: Optional[Candidate] = None
    best_score = 0.0
    for osm in index.neighbours(tomtom):
        if SOURCE_TOMTOM in osm.sources:
            continue  # this OSM record already matched another TomTom record
        if haversine_m(osm.lat, osm.lon, tomtom.lat, tomtom.lon) > max_distance_m:
            continue
        score = name_similarity(osm.name, tomtom.name)
        if score >= name_threshold and score > best_score:
            best, best_score = osm, score
    if best is not None:
        best.add_note(f"matched OSM+TomTom (similarity {best_score:.2f})")
    return best


def _absorb_tomtom_data(target: Candidate, tomtom: Candidate) -> None:
    """Copy the matched TomTom record's data onto the OSM record."""
    target.sources.append(SOURCE_TOMTOM)
    target.tomtom_id = tomtom.tomtom_id
    target.tomtom_url = tomtom.tomtom_url
    target.tomtom_address = tomtom.tomtom_address
    target.tomtom_phone = tomtom.tomtom_phone
    target.tomtom_category = tomtom.tomtom_category


def merge_sources(osm_records: List[Candidate], tomtom_records: List[Candidate],
                  *, merge_distance_m: float, name_threshold: float) -> List[Candidate]:
    """Merge both sources by name similarity within merge_distance_m (FR-10).

    Matches collapse into a single record (`sources: osm+tomtom`); unmatched
    TomTom records are appended as NEW entries — that is how businesses absent
    from OSM enter the pipeline at all.
    """
    merged: List[Candidate] = list(osm_records)
    index = _SpatialIndex(merge_distance_m)
    for record in merged:
        index.add(record)

    matched = 0
    for tomtom in tomtom_records:
        match = _find_match(index, tomtom, merge_distance_m, name_threshold)
        if match is not None:
            _absorb_tomtom_data(match, tomtom)
            matched += 1
        else:
            tomtom.add_note("found only in TomTom (no OSM record)")
            merged.append(tomtom)
            index.add(tomtom)

    only_osm = sum(1 for c in merged if c.sources == ["osm"])
    only_tomtom = sum(1 for c in merged if c.sources == [SOURCE_TOMTOM])
    log(f"Merge: {len(merged)} unique businesses "
        f"(OSM only: {only_osm}, TomTom only: {only_tomtom}, both: {matched})",
        level="ok")
    return merged


def split_by_website(records: List[Candidate]) -> Tuple[List[Candidate], List[Candidate]]:
    """Split the merged set in two -> (no website, has website).

    To count as "no website", BOTH the OSM website tag AND the TomTom poi.url
    must be empty (or contain nothing but a social-media / directory link).
    """
    no_website: List[Candidate] = []
    has_website: List[Candidate] = []

    for record in records:
        url, found_via = record.website_evidence()
        if url:
            note = ("OSM website tag present" if found_via == "osm_tag"
                    else "TomTom poi.url present")
            record.mark_has_website(url, found_via, note)
            has_website.append(record)
        else:
            no_website.append(record)

    log(f"Website filter: {len(no_website)} candidates without a site, "
        f"{len(has_website)} businesses have one.", level="ok")
    return no_website, has_website
