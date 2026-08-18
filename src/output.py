# -*- coding: utf-8 -*-
"""Cikti katmani: iki CSV + NOTLAR.txt + terminal ozeti.

Tek sorumluluk: dosya/ekran yazmak. Hicbir API cagirmaz, veri donusturmez
(sadece hazir `Candidate` kayitlarini satira cevirir).
"""

from __future__ import annotations

import csv
import os
from typing import Any, Dict, List, Sequence, Tuple

from .console import log, wrap
from .models import (
    FOUND_VIA_LANGSEARCH,
    FOUND_VIA_OSM,
    FOUND_VIA_TOMTOM,
    Candidate,
    RunResult,
)
from .text_utils import tr_locative

# --- Semalar (FR-12 / FR-13) --------------------------------------------------
NO_WEBSITE_FIELDS = [
    "name", "type", "address", "phone", "distance_m", "sources", "osm_link",
]
HAS_WEBSITE_FIELDS = [
    "name", "type", "address", "phone", "distance_m", "website", "found_via", "sources",
]
# --full-columns ile eklenen teshis kolonlari (varsayilan: eklenmez)
EXTRA_FIELDS = [
    "tomtom_checked", "langsearch_checked", "email", "opening_hours",
    "lat", "lon", "osm_id", "osm_tag", "tomtom_id", "tomtom_category",
    "social", "notes",
]

COVERAGE_NOTE = (
    "Bu liste OpenStreetMap + TomTom kapsamindaki isletmeleri icerir. "
    "Her iki platformda da kaydi olmayan isletmeler (yeni acilmis, hicbir "
    "haritaya kaydolmamis, tamamen yerel/sozlu pazarlamayla giden) bu "
    "taramada GORUNMEZ. Bu, ucretsiz veri kaynaklarinin yapisal bir sinindir."
)


def output_paths(base: str) -> Tuple[str, str, str]:
    """Taban addan uc ciktinin yolunu turetir (FR-12)."""
    root, ext = os.path.splitext(base)
    for suffix in ("_no_website", "_has_website"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
    ext = ext or ".csv"
    return f"{root}_no_website{ext}", f"{root}_has_website{ext}", f"{root}_NOTLAR.txt"


def _row_of(candidate: Candidate) -> Dict[str, Any]:
    """Kaydin tum olasi kolonlari; yazici gerekli olanlari secer."""
    return {
        "name": candidate.name,
        "type": candidate.biz_type,
        "address": candidate.best_address(),
        "phone": candidate.best_phone(),
        "distance_m": candidate.distance_m,
        "sources": "+".join(candidate.sources),
        "osm_link": candidate.osm_link,
        "website": candidate.website,
        "found_via": candidate.found_via,
        "tomtom_checked": str(candidate.tomtom_checked).lower(),
        "langsearch_checked": str(candidate.langsearch_checked).lower(),
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
    """CSV yazar — dosyaya SADECE header + veri satirlari girer (FR-14).

    Kapsam notu bilerek buraya konmaz: serbest metin satiri csv parser'larini
    ve Excel'in kolon hizalamasini bozar, yani teslim edilecek dosyayi bozar.
    """
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    # utf-8-sig: Excel Turkce karakterleri dogru acsin
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for candidate in rows:
            writer.writerow(_row_of(candidate))
    log(f"Yazildi: {path}  ({len(rows)} satir)", level="ok")


def write_notes(path: str, result: RunResult, no_website_path: str, has_website_path: str) -> None:
    """Kapsam notunu ve bu calistirmanin sayilarini ayri dosyaya yazar (FR-14)."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("KAPSAM NOTU\n")
        fh.write("=" * 70 + "\n\n")
        for line in wrap(COVERAGE_NOTE, 70):
            fh.write(line + "\n")
        fh.write("\n" + "-" * 70 + "\n")
        fh.write("BU CALISTIRMANIN CIKTILARI\n")
        fh.write("-" * 70 + "\n\n")
        fh.write(f"  Merkez: {result.center.label}\n")
        fh.write(f"  {result.scanned} isletme tarandi.\n")
        fh.write(f"  {len(result.no_website)} tanesinde website YOK  -> {os.path.basename(no_website_path)}\n")
        fh.write(f"  {len(result.has_website)} tanesinde website VAR -> {os.path.basename(has_website_path)}\n\n")
        fh.write("`sources` sutunu kaydin GERCEKTEN BULUNDUGU kaynaklari gosterir:\n")
        fh.write("  osm         -> sadece OpenStreetMap'te bulundu\n")
        fh.write("  tomtom      -> sadece TomTom'da bulundu (OSM'de kaydi yok)\n")
        fh.write("  osm+tomtom  -> her iki kaynakta da var (en guvenilir kayitlar)\n\n")
        fh.write("`found_via` sutunu website'in HANGI katmanda bulundugunu gosterir:\n")
        fh.write(f"  {FOUND_VIA_OSM:15s} -> OSM website / contact:website tag'i\n")
        fh.write(f"  {FOUND_VIA_TOMTOM:15s} -> TomTom poi.url alani\n")
        fh.write(f"  {FOUND_VIA_LANGSEARCH:15s} -> LangSearch web aramasi\n")
    log(f"Yazildi: {path}", level="ok")


def write_results(result: RunResult, *, base: str, write_has_website: bool,
                  full_columns: bool) -> Tuple[str, str, str]:
    """Uc ciktiyi da yazar ve yollarini doner."""
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
# Terminal ozeti
# ---------------------------------------------------------------------------

def _source_breakdown(records: Sequence[Candidate]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        key = "+".join(record.sources) if record.sources else "osm"
        counts[key] = counts.get(key, 0) + 1
    return counts


def print_summary(result: RunResult, *, no_web_path: str, has_web_path: str,
                  notes_path: str, wrote_has_website: bool, radius_m: int,
                  type_count: int) -> None:
    """Calisma ozeti (FR-15) + kapsam notu tekrari (FR-14)."""
    leads = result.no_website
    print()
    print("=" * 70)
    print(f"OZET  ({result.elapsed_s:.1f} sn)")
    print("=" * 70)
    print(f"Merkez                 : {result.center.label}")
    print(f"Yaricap / tip sayisi   : {radius_m} m / {type_count}")
    print(f"TomTom modu            : {result.tomtom_mode_used}")
    print()
    print(f"WEBSITE YOK (lead)     : {len(leads)}   -> {no_web_path}")
    print(f"WEBSITE VAR            : {len(result.has_website)}   -> "
          f"{has_web_path if wrote_has_website else '(yazilmadi)'}")
    for via in (FOUND_VIA_OSM, FOUND_VIA_TOMTOM, FOUND_VIA_LANGSEARCH):
        count = sum(1 for c in result.has_website if c.found_via == via)
        if count:
            print(f"   - {via:22s}: {count}")

    print()
    breakdown = _source_breakdown(leads)
    print("Lead kaynak dagilimi   : " + (", ".join(f"{k}={v}" for k, v in sorted(breakdown.items())) or "-"))
    only_tomtom = sum(1 for c in leads if c.sources == ["tomtom"])
    if only_tomtom:
        print(f"   -> {only_tomtom} lead SADECE TomTom sayesinde bulundu (OSM'de yoktu)")

    with_phone = sum(1 for c in leads if c.best_phone())
    with_address = sum(1 for c in leads if c.best_address())
    pct = (100 * with_phone / len(leads)) if leads else 0
    print(f"Telefonu dolu lead     : {with_phone}/{len(leads)}  (%{pct:.0f})")
    print(f"Adresi dolu lead       : {with_address}/{len(leads)}")
    if result.tomtom_request_limit:
        print(f"TomTom istek kullanimi : {result.tomtom_requests_used}/{result.tomtom_request_limit}")
    print("Kullanilan istekler    : " + ", ".join(
        f"{k}={v}" for k, v in sorted(result.request_counts.items())))

    print("-" * 70)
    print("KAPSAM NOTU:")
    for line in wrap(COVERAGE_NOTE, 68):
        print("  " + line)
    print(f"  (ayrica yazildi: {notes_path})")
    print("-" * 70)
    print(f"{result.scanned} işletme tarandı, "
          f"{tr_locative(len(leads))} website yok, "
          f"{tr_locative(len(result.has_website))} website var.")
    print("=" * 70)
