# -*- coding: utf-8 -*-
"""Ust seviye orkestrasyon.

Bu dosya INCE olmali: is mantigi burada degil, ilgili modullerde durur.
Bastan sona okunca algoritmanin ozeti gorulmeli.

    geocode -> OSM kesfi -> TomTom kesfi -> birlestir -> website ayrimi
            -> LangSearch dogrulamasi -> ciktilari yaz
"""

from __future__ import annotations

import time
from typing import List, Optional, Sequence, Tuple

from . import geocode, langsearch_verify, merge, osm_source, output, tomtom_source
from .config import Config
from .console import log
from .http_client import Budget, HttpClient
from .models import Candidate, Center, RunResult


def resolve_center(http: HttpClient, cfg: Config, *, address: Optional[str],
                   latlng: Optional[Tuple[float, float]]) -> Optional[Center]:
    """Merkez noktayi belirler: dogrudan koordinat ya da adres cozumleme."""
    if latlng is not None:
        lat, lon = latlng
        return Center(lat=lat, lon=lon, label=f"{lat:.6f},{lon:.6f}")
    if not address:
        return None
    return geocode.geocode_address(http, cfg, address)


def _discover_tomtom(http: HttpClient, cfg: Config, types: Sequence[str],
                     center: Center, budget: Budget) -> List[Candidate]:
    """discover modunda TomTom'u bagimsiz kesif kaynagi olarak calistirir."""
    if not (cfg.tomtom_enabled and cfg.tomtom_mode == "discover"):
        return []
    return tomtom_source.search_tomtom_discover(http, cfg, types, center, budget)


def _verify_tomtom(http: HttpClient, cfg: Config, candidates: List[Candidate],
                   budget: Budget) -> List[Candidate]:
    """verify modunda adaylari tek tek TomTom'a sorar (yeni isletme eklemez)."""
    if not (cfg.tomtom_enabled and cfg.tomtom_mode == "verify") or not candidates:
        return candidates
    return tomtom_source.verify_with_tomtom(http, cfg, candidates, budget)


def _verify_langsearch(http: HttpClient, cfg: Config, center: Center,
                       candidates: List[Candidate]) -> Tuple[List[Candidate], List[Candidate]]:
    """Son capraz kontrol -> (hala sitesiz, sitesi bulunanlar)."""
    if not cfg.langsearch_enabled or not candidates:
        if not cfg.langsearch_enabled:
            log("KATMAN 3 atlandi (LangSearch kapali veya anahtar yok).")
        return candidates, []
    city = cfg.langsearch_city
    if city is None:
        city = geocode.reverse_city(http, cfg, center.lat, center.lon)
        log(f'Katman 3 konum eki: "{city}"', level="dbg")
    return langsearch_verify.verify_with_langsearch(http, cfg, candidates, city or "")


def _mark_all_tomtom_checked(records: Sequence[Candidate]) -> None:
    """discover modunda tum alan tarandi -> her kayit TomTom'a karsi kontrol edildi."""
    for record in records:
        record.tomtom_checked = True


def run(cfg: Config, types: Sequence[str], *, address: Optional[str] = None,
        latlng: Optional[Tuple[float, float]] = None) -> Optional[RunResult]:
    """Pipeline'i bastan sona calistirir. Merkez cozulemezse None."""
    started = time.time()
    http = HttpClient(cfg)
    budget = Budget(cfg.tomtom_daily_limit)

    center = resolve_center(http, cfg, address=address, latlng=latlng)
    if center is None:
        return None

    # --- KATMAN 1: iki bagimsiz kesif kaynagi ---
    osm_records = osm_source.search_osm(http, cfg, types, center)
    tomtom_records = _discover_tomtom(http, cfg, types, center, budget)

    # --- Birlestirme ---
    merged = merge.merge_sources(
        osm_records, tomtom_records,
        merge_distance_m=cfg.merge_distance_m,
        name_threshold=cfg.name_threshold,
    )
    if tomtom_records:
        _mark_all_tomtom_checked(merged)

    # --- verify modu: adaylari zenginlestir/dogrula ---
    merged = _verify_tomtom(http, cfg, merged, budget)

    # --- Website ayrimi (tek karar noktasi) ---
    no_website, has_website = merge.split_by_website(merged)

    # --- KATMAN 3: son capraz kontrol ---
    #no_website, found_by_langsearch = _verify_langsearch(http, cfg, center, no_website)
    #has_website.extend(found_by_langsearch)

    return RunResult(
        center=center,
        no_website=no_website,
        has_website=has_website,
        tomtom_requests_used=budget.used,
        tomtom_request_limit=budget.limit if cfg.tomtom_enabled else 0,
        tomtom_mode_used=cfg.tomtom_mode if cfg.tomtom_enabled else "kullanilmadi",
        request_counts=dict(http.counts),
        elapsed_s=time.time() - started,
    )


def run_and_report(cfg: Config, types: Sequence[str], *, address: Optional[str] = None,
                   latlng: Optional[Tuple[float, float]] = None) -> int:
    """run() + ciktilari yaz + ozeti bas. CLI'nin cagirdigi tek fonksiyon."""
    result = run(cfg, types, address=address, latlng=latlng)
    if result is None:
        return 1

    if result.scanned == 0:
        log("Hicbir isletme bulunamadi. Yaricapi buyutmeyi veya baska is tipleri "
            "denemeyi dusunun.", level="warn")

    no_web_path, has_web_path, notes_path = output.write_results(
        result, base=cfg.output_base,
        write_has_website=cfg.write_has_website,
        full_columns=cfg.full_columns,
    )
    output.print_summary(
        result,
        no_web_path=no_web_path,
        has_web_path=has_web_path,
        notes_path=notes_path,
        wrote_has_website=cfg.write_has_website,
        radius_m=cfg.radius_m,
        type_count=len(types),
    )
    return 0
