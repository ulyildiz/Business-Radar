# -*- coding: utf-8 -*-
"""OSM + TomTom sonuclarini birlestirme (dedup) ve website ayrimi.

Tek sorumluluk: kayit kumelerini birlestirmek ve "website var mi" kararini
TEK yerde vermek. Ag istegi atmaz, dosya yazmaz.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .console import log
from .geo_utils import haversine_m, meters_to_deg
from .models import SOURCE_TOMTOM, Candidate
from .text_utils import name_similarity

Bucket = Tuple[int, int]


class _SpatialIndex:
    """Kaba konum kovalari — her kaydi herkesle karsilastirmayi onler."""

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
    """TomTom kaydiyla ayni isletme olan OSM kaydini bulur (isim + yakinlik)."""
    best: Optional[Candidate] = None
    best_score = 0.0
    for osm in index.neighbours(tomtom):
        if SOURCE_TOMTOM in osm.sources:
            continue  # bu OSM kaydi zaten baska bir TomTom kaydiyla eslesti
        if haversine_m(osm.lat, osm.lon, tomtom.lat, tomtom.lon) > max_distance_m:
            continue
        score = name_similarity(osm.name, tomtom.name)
        if score >= name_threshold and score > best_score:
            best, best_score = osm, score
    if best is not None:
        best.add_note(f"OSM+TomTom eslesti (benzerlik {best_score:.2f})")
    return best


def _absorb_tomtom_data(target: Candidate, tomtom: Candidate) -> None:
    """Eslesen TomTom kaydinin verisini OSM kaydina tasir."""
    target.sources.append(SOURCE_TOMTOM)
    target.tomtom_id = tomtom.tomtom_id
    target.tomtom_url = tomtom.tomtom_url
    target.tomtom_address = tomtom.tomtom_address
    target.tomtom_phone = tomtom.tomtom_phone
    target.tomtom_category = tomtom.tomtom_category


def merge_sources(osm_records: List[Candidate], tomtom_records: List[Candidate],
                  *, merge_distance_m: float, name_threshold: float) -> List[Candidate]:
    """Iki kaynagi isim + <=merge_distance_m yakinligiyla birlestirir (FR-10).

    Eslesenler tek kayda iner (`sources: osm+tomtom`), eslesmeyen TomTom
    kayitlari listeye YENI girdi olarak eklenir — OSM'de olmayan isletmeler
    boylece pipeline'a girer.
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
            tomtom.add_note("Sadece TomTom'da bulundu (OSM'de kaydi yok)")
            merged.append(tomtom)
            index.add(tomtom)

    only_osm = sum(1 for c in merged if c.sources == ["osm"])
    only_tomtom = sum(1 for c in merged if c.sources == [SOURCE_TOMTOM])
    log(f"Birlestirme: {len(merged)} benzersiz isletme "
        f"(sadece OSM: {only_osm}, sadece TomTom: {only_tomtom}, ikisi birden: {matched})",
        level="ok")
    return merged


def split_by_website(records: List[Candidate]) -> Tuple[List[Candidate], List[Candidate]]:
    """Birlesik seti ikiye ayirir -> (website yok, website var).

    "Website yok" sayilmasi icin HEM OSM website tag'i HEM DE TomTom poi.url
    bos olmali (ya da yalnizca sosyal medya/dizin linki olmali).
    """
    no_website: List[Candidate] = []
    has_website: List[Candidate] = []

    for record in records:
        url, found_via = record.website_evidence()
        if url:
            note = ("OSM website tag'i mevcut" if found_via == "osm_tag"
                    else "TomTom poi.url mevcut")
            record.mark_has_website(url, found_via, note)
            has_website.append(record)
        else:
            no_website.append(record)

    log(f"Website filtresi: {len(no_website)} sitesiz aday, "
        f"{len(has_website)} isletmenin sitesi var.", level="ok")
    return no_website, has_website
