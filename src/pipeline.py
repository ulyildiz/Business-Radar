# -*- coding: utf-8 -*-
"""Top-level orchestration.

This file must stay THIN: no business logic lives here, only in the modules it
calls. Reading it top to bottom should give you a summary of the algorithm.

    geocode -> OSM discovery -> TomTom discovery -> merge -> website split
            -> write outputs
"""

from __future__ import annotations

import time
from typing import List, Optional, Sequence, Tuple

from . import geocode, merge, osm_source, output, tomtom_source
from .config import Config
from .console import log
from .http_client import Budget, HttpClient
from .models import Candidate, Center, RunResult


def resolve_center(http: HttpClient, cfg: Config, *, address: Optional[str],
                   latlng: Optional[Tuple[float, float]]) -> Optional[Center]:
    """Determine the center point: explicit coordinates or a geocoded address."""
    if latlng is not None:
        lat, lon = latlng
        return Center(lat=lat, lon=lon, label=f"{lat:.6f},{lon:.6f}")
    if not address:
        return None
    return geocode.geocode_address(http, cfg, address)


def _discover_tomtom(http: HttpClient, cfg: Config, types: Sequence[str],
                     center: Center, budget: Budget) -> List[Candidate]:
    """In discover mode, run TomTom as an independent discovery source."""
    if not (cfg.tomtom_enabled and cfg.tomtom_mode == "discover"):
        return []
    return tomtom_source.search_tomtom_discover(http, cfg, types, center, budget)


def _verify_tomtom(http: HttpClient, cfg: Config, candidates: List[Candidate],
                   budget: Budget) -> List[Candidate]:
    """In verify mode, query TomTom per candidate (adds no new businesses)."""
    if not (cfg.tomtom_enabled and cfg.tomtom_mode == "verify") or not candidates:
        return candidates
    return tomtom_source.verify_with_tomtom(http, cfg, candidates, budget)


def _mark_all_tomtom_checked(records: Sequence[Candidate]) -> None:
    """In discover mode the whole area was swept, so every record was checked."""
    for record in records:
        record.tomtom_checked = True


def run(cfg: Config, types: Sequence[str], *, address: Optional[str] = None,
        latlng: Optional[Tuple[float, float]] = None) -> Optional[RunResult]:
    """Run the pipeline end to end. Returns None if the center cannot resolve."""
    started = time.time()
    http = HttpClient(cfg)
    budget = Budget(cfg.tomtom_daily_limit)

    center = resolve_center(http, cfg, address=address, latlng=latlng)
    if center is None:
        return None

    # --- LAYER 1: two independent discovery sources ---
    osm_records = osm_source.search_osm(http, cfg, types, center)
    tomtom_records = _discover_tomtom(http, cfg, types, center, budget)

    # --- Merge ---
    merged = merge.merge_sources(
        osm_records, tomtom_records,
        merge_distance_m=cfg.merge_distance_m,
        name_threshold=cfg.name_threshold,
    )
    if tomtom_records:
        _mark_all_tomtom_checked(merged)

    # --- verify mode: enrich / confirm the candidates ---
    merged = _verify_tomtom(http, cfg, merged, budget)

    # --- Website split (the single decision point) ---
    no_website, has_website = merge.split_by_website(merged)

    return RunResult(
        center=center,
        no_website=no_website,
        has_website=has_website,
        tomtom_requests_used=budget.used,
        tomtom_request_limit=budget.limit if cfg.tomtom_enabled else 0,
        tomtom_mode_used=cfg.tomtom_mode if cfg.tomtom_enabled else "unused",
        request_counts=dict(http.counts),
        elapsed_s=time.time() - started,
    )


def run_and_report(cfg: Config, types: Sequence[str], *, address: Optional[str] = None,
                   latlng: Optional[Tuple[float, float]] = None) -> int:
    """run() + write outputs + print the summary. The CLI's single entry point."""
    result = run(cfg, types, address=address, latlng=latlng)
    if result is None:
        return 1

    if result.scanned == 0:
        log("No businesses found. Consider widening the radius or trying other "
            "business types.", level="warn")

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
