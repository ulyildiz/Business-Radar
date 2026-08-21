# -*- coding: utf-8 -*-
"""Data types carried through the pipeline.

Single responsibility: the record schema. Every layer (osm_source,
tomtom_source, merge, langsearch_verify, output) enriches and passes on the
same `Candidate` object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from .text_utils import is_real_website, norm_text

# --- Source tags (Candidate.sources) ---
SOURCE_OSM = "osm"
SOURCE_TOMTOM = "tomtom"

# --- Which layer found the website (has_website.csv -> found_via) ---
FOUND_VIA_OSM = "osm_tag"
FOUND_VIA_TOMTOM = "tomtom_poi_url"
FOUND_VIA_LANGSEARCH = "langsearch"

# --- Whether the lead was verified (no_website.csv -> verified) ---
# "langsearch"  -> searched the web, found NO domain of its own (strong lead)
# "not_checked" -> could not verify: layer disabled, quota exhausted, or the
#                  request failed. The record stays a lead, but with weaker
#                  evidence behind it.
VERIFY_LANGSEARCH = FOUND_VIA_LANGSEARCH
VERIFY_NOT_CHECKED = "not_checked"


@dataclass
class Candidate:
    """A single business record (same schema regardless of source)."""

    name: str = ""
    biz_type: str = ""
    lat: float = 0.0
    lon: float = 0.0
    distance_m: int = 0
    address: str = ""
    phone: str = ""
    email: str = ""
    opening_hours: str = ""
    social: str = ""
    # --- OSM side ---
    osm_type: str = ""
    osm_id: int = 0
    osm_tags_matched: str = ""
    osm_link: str = ""
    osm_website: str = ""
    # --- TomTom side ---
    tomtom_id: str = ""
    tomtom_url: str = ""
    tomtom_address: str = ""
    tomtom_phone: str = ""
    tomtom_category: str = ""
    # --- audit trail ---
    sources: List[str] = field(default_factory=list)
    tomtom_checked: bool = False
    langsearch_checked: bool = False
    langsearch_skipped: bool = False
    website: str = ""
    found_via: str = ""
    notes: str = ""

    @property
    def dedup_key(self) -> str:
        """Collapses node/way/relation duplicates of the same business."""
        return f"{norm_text(self.name)}|{round(self.lat, 4)}|{round(self.lon, 4)}"

    def add_note(self, text: str) -> None:
        self.notes = f"{self.notes}; {text}" if self.notes else text

    def website_evidence(self) -> Tuple[str, str]:
        """Does this record have a real website? -> (url, found_via) or ("", "")"""
        if self.osm_website and is_real_website(self.osm_website):
            return self.osm_website, FOUND_VIA_OSM
        if self.tomtom_url and is_real_website(self.tomtom_url):
            url = self.tomtom_url
            return (url if url.startswith("http") else "http://" + url), FOUND_VIA_TOMTOM
        return "", ""

    def mark_has_website(self, url: str, found_via: str, note: str = "") -> None:
        self.website = url
        self.found_via = found_via
        if note:
            self.add_note(note)

    def verification_state(self) -> str:
        """Did this lead pass through Layer 3? (no_website.csv -> verified)"""
        return VERIFY_LANGSEARCH if self.langsearch_checked else VERIFY_NOT_CHECKED

    def mark_unverified(self, reason: str) -> None:
        """Verification was not possible — recorded rather than passed over."""
        self.langsearch_skipped = True
        self.add_note(f"LangSearch verification skipped ({reason})")

    def best_phone(self) -> str:
        return self.phone or self.tomtom_phone

    def best_address(self) -> str:
        return self.address or self.tomtom_address


@dataclass
class Center:
    """The center point of the scan."""

    lat: float
    lon: float
    label: str


@dataclass
class RunResult:
    """The outcome of one run — the output layer turns this into a report."""

    center: Center
    no_website: List[Candidate] = field(default_factory=list)
    has_website: List[Candidate] = field(default_factory=list)
    tomtom_requests_used: int = 0
    tomtom_request_limit: int = 0
    tomtom_mode_used: str = "unused"
    request_counts: dict = field(default_factory=dict)
    elapsed_s: float = 0.0

    @property
    def scanned(self) -> int:
        return len(self.no_website) + len(self.has_website)
