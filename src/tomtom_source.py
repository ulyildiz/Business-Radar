# -*- coding: utf-8 -*-
"""LAYER 1b / 2 — TomTom Search.

Two modes:
  discover (default) — a full area sweep INDEPENDENT of OSM; it ADDS
                       businesses that have no OSM record at all.
  verify             — checks only the supplied candidates one by one; adds
                       no new businesses and is cheaper on quota.

Category search is TEXT based, not the numeric `categorySet`: a wrong numeric
ID raises no error, it silently returns an empty result set that cannot be
told apart from "there are no businesses in this area". Silent failure is the
most dangerous kind.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from .config import Config
from .console import log
from .geo_utils import build_grid, haversine_m
from .http_client import Budget, HttpClient
from .models import SOURCE_TOMTOM, Candidate, Center
from .text_utils import name_similarity

TOMTOM_CATEGORY_SEARCH = "https://api.tomtom.com/search/2/categorySearch/{query}.json"
TOMTOM_POI_SEARCH = "https://api.tomtom.com/search/2/poiSearch/{query}.json"
BUCKET = "tomtom"

# ---------------------------------------------------------------------------
# Business type -> TomTom category search text.
# Override per type with --tomtom-category <type>="<text>" when local
# terminology returns better results than the English default.
# ---------------------------------------------------------------------------
TOMTOM_CATEGORIES: Dict[str, str] = {
    "restaurant": "restaurant", "cafe": "cafe", "bar": "bar",
    "fast_food": "fast food", "bakery": "bakery", "patisserie": "patisserie",
    "butcher": "butcher", "greengrocer": "greengrocer", "supermarket": "supermarket",
    "hair_salon": "hairdresser", "beauty_salon": "beauty salon", "spa": "spa",
    "tattoo": "tattoo parlour",
    "car_repair": "car repair", "car_dealer": "car dealer", "car_wash": "car wash",
    "car_parts": "car parts", "tyres": "tyre service", "motorcycle": "motorcycle dealer",
    "dentist": "dentist", "doctor": "doctor", "pharmacy": "pharmacy",
    "veterinary": "veterinarian", "physiotherapist": "physiotherapist",
    "optician": "optician",
    "lawyer": "lawyer", "accountant": "accountant", "estate_agent": "real estate agency",
    "insurance": "insurance", "travel_agency": "travel agency", "architect": "architect",
    "advertising": "advertising agency",
    "plumber": "plumber", "electrician": "electrician", "carpenter": "carpenter",
    "painter": "painter", "locksmith": "locksmith", "hvac": "heating and cooling",
    "photographer": "photographer",
    "gym": "fitness centre", "sports_centre": "sports centre",
    "driving_school": "driving school", "language_school": "language school",
    "kindergarten": "kindergarten", "hotel": "hotel",
    "florist": "florist", "jewelry": "jewellery", "clothes": "clothing store",
    "shoes": "shoe shop", "furniture": "furniture store", "hardware": "hardware store",
    "computer": "computer store", "mobile_phone": "mobile phone shop",
    "laundry": "laundry", "pet_shop": "pet shop", "bookshop": "bookshop",
    "bicycle": "bicycle shop", "copyshop": "copy shop", "funeral": "funeral home",
}


def category_for(btype: str, overrides: Dict[str, str]) -> str:
    """Search text for a business type (override > default > the type name)."""
    return overrides.get(btype) or TOMTOM_CATEGORIES.get(btype, btype.replace("_", " "))


def _poi_to_candidate(result: Dict[str, Any], btype: str, center: Center,
                      radius_m: int) -> Optional[Candidate]:
    """Convert a TomTom result into a Candidate; None if unnamed or too far."""
    poi = result.get("poi") or {}
    name = (poi.get("name") or "").strip()
    if not name:
        return None

    position = result.get("position") or {}
    lat, lon = position.get("lat"), position.get("lon")
    if lat is None or lon is None:
        return None

    distance = haversine_m(center.lat, center.lon, float(lat), float(lon))
    if distance > radius_m:
        return None  # a grid cell can overhang the search circle

    address = result.get("address") or {}
    return Candidate(
        name=name,
        biz_type=btype,
        lat=float(lat),
        lon=float(lon),
        distance_m=int(round(distance)),
        tomtom_id=str(result.get("id", "")),
        tomtom_url=(poi.get("url") or "").strip(),
        tomtom_address=address.get("freeformAddress", ""),
        tomtom_phone=(poi.get("phone") or "").strip(),
        tomtom_category=", ".join(str(c) for c in (poi.get("categories") or [])),
        sources=[SOURCE_TOMTOM],
    )


def _search_cell(http: HttpClient, cfg: Config, lat: float, lon: float,
                 category: str, budget: Budget) -> Tuple[List[Dict[str, Any]], bool]:
    """Scan one grid cell, paging through results. -> (results, saturated)"""
    url = TOMTOM_CATEGORY_SEARCH.format(query=requests.utils.quote(category, safe=""))
    collected: List[Dict[str, Any]] = []
    limit = min(cfg.tomtom_limit, 100)
    saturated = False

    for page in range(max(1, cfg.tomtom_max_pages)):
        if not budget.take():
            return collected, saturated
        resp = http.request(
            "GET", url,
            bucket=BUCKET, delay=cfg.delay_tomtom,
            params={
                "key": cfg.tomtom_key,
                "lat": f"{lat:.6f}",
                "lon": f"{lon:.6f}",
                "radius": cfg.tomtom_cell_radius_m,
                "limit": limit,
                "ofs": page * limit,
                "language": cfg.tomtom_language,
            },
        )
        if resp is None:
            break
        try:
            results = resp.json().get("results", [])
        except ValueError:
            break
        collected.extend(results)
        if len(results) < limit:
            break
        if page == max(1, cfg.tomtom_max_pages) - 1:
            saturated = True  # page limit reached; results may have been truncated
    return collected, saturated


def _warn_if_over_budget(planned: int, budget: Budget) -> None:
    if planned > budget.left:
        log(f"Planned requests ({planned}) exceed the remaining quota "
            f"({budget.left}). The scan will stop when the quota runs out — "
            f"consider raising --tomtom-cell-radius or lowering --radius.",
            level="warn")


def search_tomtom_discover(http: HttpClient, cfg: Config, types: Sequence[str],
                           center: Center, budget: Budget) -> List[Candidate]:
    """Run TomTom as a discovery source independent of OSM (FR-5)."""
    cells = build_grid(center.lat, center.lon, cfg.radius_m, cfg.tomtom_cell_radius_m)
    planned = len(cells) * len(types)
    log(f"LAYER 1b (TomTom discovery) — {len(cells)} cells x {len(types)} "
        f"categories = ~{planned} requests (quota left: {budget.left})")
    _warn_if_over_budget(planned, budget)

    found: Dict[str, Candidate] = {}
    saturated_cells = 0

    for index, btype in enumerate(types, 1):
        category = category_for(btype, cfg.tomtom_categories)
        for cell_no, (clat, clon) in enumerate(cells, 1):
            if budget.left <= 0:
                break
            results, saturated = _search_cell(http, cfg, clat, clon, category, budget)
            saturated_cells += 1 if saturated else 0

            for result in results:
                candidate = _poi_to_candidate(result, btype, center, cfg.radius_m)
                if candidate is None:
                    continue
                existing = found.get(candidate.dedup_key)
                if existing is None or (not existing.tomtom_url and candidate.tomtom_url):
                    found[candidate.dedup_key] = candidate

            if cell_no % 10 == 0:
                log(f"  [{btype}] cell {cell_no}/{len(cells)}, "
                    f"{len(found)} businesses, {budget.used} requests", level="dbg")
        log(f"  [{index}/{len(types)}] {btype}: {len(found)} businesses so far, "
            f"{budget.used} requests", level="dbg")

    results_list = sorted(found.values(), key=lambda c: c.distance_m)
    with_url = sum(1 for c in results_list if c.tomtom_url)
    log(f"LAYER 1b (TomTom) result: {len(results_list)} businesses "
        f"({with_url} have a poi.url). Requests used: {budget.used}", level="ok")
    if saturated_cells:
        log(f"{saturated_cells} cells came back saturated — results there may be "
            f"truncated. Lower --tomtom-cell-radius or raise --tomtom-max-pages.",
            level="warn")
    return results_list


def _best_match(results: Sequence[Dict[str, Any]], name: str,
                threshold: float) -> Tuple[Optional[Dict[str, Any]], float]:
    """The best POI above the name-similarity threshold."""
    best, best_score = None, 0.0
    for result in results:
        poi_name = (result.get("poi") or {}).get("name") or ""
        score = name_similarity(name, poi_name)
        if score >= threshold and score > best_score:
            best, best_score = result, score
    return best, best_score


def _enrich_from_poi(candidate: Candidate, match: Dict[str, Any]) -> None:
    """Copy phone / address / url over from the matched TomTom record."""
    poi = match.get("poi") or {}
    address = match.get("address") or {}
    if SOURCE_TOMTOM not in candidate.sources:
        candidate.sources.append(SOURCE_TOMTOM)
    candidate.tomtom_id = str(match.get("id", ""))
    candidate.tomtom_url = (poi.get("url") or "").strip()
    if not candidate.address and address.get("freeformAddress"):
        candidate.tomtom_address = address["freeformAddress"]
    if not candidate.phone and poi.get("phone"):
        candidate.tomtom_phone = poi["phone"]


def _query_poi(http: HttpClient, cfg: Config, candidate: Candidate) -> Optional[List[Dict[str, Any]]]:
    """POI search for one candidate. None if no response, [] if no results."""
    url = TOMTOM_POI_SEARCH.format(query=requests.utils.quote(candidate.name[:100], safe=""))
    resp = http.request(
        "GET", url,
        bucket=BUCKET, delay=cfg.delay_tomtom,
        params={
            "key": cfg.tomtom_key,
            "lat": f"{candidate.lat:.6f}",
            "lon": f"{candidate.lon:.6f}",
            "radius": cfg.tomtom_match_radius_m,
            "limit": 10,
            "language": cfg.tomtom_language,
        },
    )
    if resp is None:
        return None
    try:
        return resp.json().get("results", [])
    except ValueError:
        return []


def _verify_one(http: HttpClient, cfg: Config, candidate: Candidate, budget: Budget) -> None:
    """Query TomTom for one candidate and enrich it if a match is found."""
    if not candidate.name:
        candidate.add_note("TomTom skipped (unnamed)")
        return
    if not budget.take():
        candidate.add_note("TomTom quota exhausted, not verified")
        return

    results = _query_poi(http, cfg, candidate)
    if results is None:
        candidate.add_note("TomTom did not respond")
        return

    candidate.tomtom_checked = True
    match, score = _best_match(results, candidate.name, cfg.name_threshold)
    if match is not None:
        _enrich_from_poi(candidate, match)
        candidate.add_note(f"matched in TomTom (similarity {score:.2f})")


def verify_with_tomtom(http: HttpClient, cfg: Config, candidates: List[Candidate],
                       budget: Budget) -> List[Candidate]:
    """`verify` mode: query each candidate and enrich it with TomTom data.

    Website detection does NOT happen here — records are only enriched. The
    split is done by the pipeline in one place (split_by_website), so the
    "does it have a website" decision stays at a single point.
    """
    log(f"LAYER 2 — TomTom verification ({len(candidates)} candidates, "
        f"quota left: {budget.left})")

    for index, candidate in enumerate(candidates, 1):
        _verify_one(http, cfg, candidate, budget)
        if index % 25 == 0:
            log(f"  ... {index}/{len(candidates)} processed ({budget.used} requests)", level="dbg")

    log(f"LAYER 2 complete. Requests used: {budget.used}", level="ok")
    return candidates


def _probe_one_type(http: HttpClient, cfg: Config, btype: str,
                    center: Center, radius_m: int, sample: int) -> bool:
    """Query one type and print the results. -> whether anything came back."""
    category = category_for(btype, cfg.tomtom_categories)
    url = TOMTOM_CATEGORY_SEARCH.format(query=requests.utils.quote(category, safe=""))
    resp = http.request(
        "GET", url, bucket=BUCKET, delay=cfg.delay_tomtom,
        params={"key": cfg.tomtom_key, "lat": f"{center.lat:.6f}",
                "lon": f"{center.lon:.6f}", "radius": radius_m,
                "limit": sample, "language": cfg.tomtom_language},
    )
    print()
    print(f'--- {btype}   (search text: "{category}")')

    if resp is None:
        print("    REQUEST FAILED")
        return False
    try:
        results = resp.json().get("results", [])
    except ValueError:
        results = []
    if not results:
        print("    !! NO RESULTS — this search text may not work in this area.")
        print(f'       Try: --tomtom-category {btype}="<local term>"')
        return False

    for result in results[:sample]:
        poi = result.get("poi") or {}
        cats = ", ".join(str(x) for x in (poi.get("categories") or []))
        has_site = "has site" if (poi.get("url") or "").strip() else "no site"
        print(f"    - {(poi.get('name') or '?')[:38]:38s} [{cats[:26]:26s}] {has_site}")
    return True


def probe_categories(http: HttpClient, cfg: Config, types: Sequence[str],
                     center: Center, sample: int = 6) -> int:
    """Show what TomTom returns for each business type (category sanity check).

    Spends exactly ONE request per type. The point is to confirm by eye, before
    a production scan, that each search text returns the intended trade.
    """
    radius = min(cfg.radius_m, 5000)
    print("=" * 72)
    print(f"TOMTOM CATEGORY CHECK — {len(types)} types, 1 request per type")
    print(f"Center: {center.lat:.5f}, {center.lon:.5f}   Radius: {radius} m")
    print("=" * 72)

    suspicious = [t for t in types
                  if not _probe_one_type(http, cfg, t, center, radius, sample)]

    print()
    print("=" * 72)
    if suspicious:
        print("ATTENTION — these types need review: " + ", ".join(suspicious))
        print("Do not run a production scan before fixing them.")
    else:
        print("Every type returned results. Confirm by eye that the names and")
        print("categories above belong to the trade you expected.")
    print("=" * 72)
    return 0
