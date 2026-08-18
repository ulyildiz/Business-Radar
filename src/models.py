# -*- coding: utf-8 -*-
"""Pipeline boyunca tasinan veri tipleri.

Tek sorumluluk: kayit semasi. Her katman (osm_source, tomtom_source, merge,
langsearch_verify, output) ayni `Candidate` nesnesini zenginlestirerek gecer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from .text_utils import is_real_website, norm_text

# --- Kaynak etiketleri (Candidate.sources) ---
SOURCE_OSM = "osm"
SOURCE_TOMTOM = "tomtom"

# --- Web sitesinin HANGI katmanda bulundugu (has_website.csv -> found_via) ---
FOUND_VIA_OSM = "osm_tag"
FOUND_VIA_TOMTOM = "tomtom_poi_url"
FOUND_VIA_LANGSEARCH = "langsearch"


@dataclass
class Candidate:
    """Tek bir isletme kaydi (kaynak ne olursa olsun ayni sema)."""

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
    # --- OSM tarafi ---
    osm_type: str = ""
    osm_id: int = 0
    osm_tags_matched: str = ""
    osm_link: str = ""
    osm_website: str = ""
    # --- TomTom tarafi ---
    tomtom_id: str = ""
    tomtom_url: str = ""
    tomtom_address: str = ""
    tomtom_phone: str = ""
    tomtom_category: str = ""
    # --- izler ---
    sources: List[str] = field(default_factory=list)
    tomtom_checked: bool = False
    langsearch_checked: bool = False
    website: str = ""
    found_via: str = ""
    notes: str = ""

    @property
    def dedup_key(self) -> str:
        """Ayni isletmenin node/way/relation kopyalarini birlestirmek icin."""
        return f"{norm_text(self.name)}|{round(self.lat, 4)}|{round(self.lon, 4)}"

    def add_note(self, text: str) -> None:
        self.notes = f"{self.notes}; {text}" if self.notes else text

    def website_evidence(self) -> Tuple[str, str]:
        """Kayitta gercek bir web sitesi var mi? -> (url, found_via) ya da ("", "")"""
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

    def best_phone(self) -> str:
        return self.phone or self.tomtom_phone

    def best_address(self) -> str:
        return self.address or self.tomtom_address


@dataclass
class Center:
    """Taramanin merkez noktasi."""

    lat: float
    lon: float
    label: str


@dataclass
class RunResult:
    """Bir calistirmanin sonucu — output katmani bunu rapora cevirir."""

    center: Center
    no_website: List[Candidate] = field(default_factory=list)
    has_website: List[Candidate] = field(default_factory=list)
    tomtom_requests_used: int = 0
    tomtom_request_limit: int = 0
    tomtom_mode_used: str = "kullanilmadi"
    request_counts: dict = field(default_factory=dict)
    elapsed_s: float = 0.0

    @property
    def scanned(self) -> int:
        return len(self.no_website) + len(self.has_website)
