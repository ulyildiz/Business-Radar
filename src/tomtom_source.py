# -*- coding: utf-8 -*-
"""KATMAN 1b / 2 — TomTom Search.

Iki mod:
  discover (varsayilan) — OSM'den BAGIMSIZ tam alan taramasi; OSM'de hic
                          kaydi olmayan isletmeleri de pipeline'a EKLER.
  verify                — sadece verilen adaylari tek tek dogrular; yeni
                          isletme eklemez, kota tasarrufludur.

Kategori aramasi METIN bazlidir (numeric `categorySet` degil): yanlis bir
numeric ID hata firlatmaz, sessizce bos sonuc doner ve "bolgede isletme yok"
ile ayirt edilemez. Sessiz basarisizlik en tehlikeli hata turudur.
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
# Is tipi -> TomTom kategori arama metni.
# --tomtom-category <tip>="<metin>" ile ezilebilir (yerel terminoloji icin).
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
    """Bir is tipinin TomTom arama metni (override > varsayilan > tipin kendisi)."""
    return overrides.get(btype) or TOMTOM_CATEGORIES.get(btype, btype.replace("_", " "))


def _poi_to_candidate(result: Dict[str, Any], btype: str, center: Center,
                      radius_m: int) -> Optional[Candidate]:
    """TomTom sonucunu Candidate'a cevirir; isimsiz/uzaksa None."""
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
        return None  # grid hucresi daire disina tasmis olabilir

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
    """Tek grid hucresini tarar (sayfalamayla). -> (sonuclar, doygun_mu)"""
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
                "language": "tr-TR",
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
            saturated = True  # sayfa limiti doldu, sonuclar kesilmis olabilir
    return collected, saturated


def _warn_if_over_budget(planned: int, budget: Budget) -> None:
    if planned > budget.left:
        log(f"Planlanan istek ({planned}) kalan kotadan ({budget.left}) fazla. "
            f"Tarama kota bitince kesilecek — --tomtom-cell-radius degerini buyutmeyi "
            f"veya --radius'u kucultmeyi dusunun.", level="warn")


def search_tomtom_discover(http: HttpClient, cfg: Config, types: Sequence[str],
                           center: Center, budget: Budget) -> List[Candidate]:
    """TomTom'u OSM'den bagimsiz bir kesif kaynagi olarak calistirir (FR-5)."""
    cells = build_grid(center.lat, center.lon, cfg.radius_m, cfg.tomtom_cell_radius_m)
    planned = len(cells) * len(types)
    log(f"KATMAN 1b (TomTom kesif) — {len(cells)} hucre x {len(types)} kategori "
        f"= ~{planned} istek (kota kalan: {budget.left})")
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
                log(f"  [{btype}] {cell_no}/{len(cells)} hucre, "
                    f"{len(found)} isletme, {budget.used} istek", level="dbg")
        log(f"  [{index}/{len(types)}] {btype}: toplam {len(found)} isletme, "
            f"{budget.used} istek", level="dbg")

    results_list = sorted(found.values(), key=lambda c: c.distance_m)
    with_url = sum(1 for c in results_list if c.tomtom_url)
    log(f"KATMAN 1b (TomTom) sonucu: {len(results_list)} isletme "
        f"({with_url} tanesinin poi.url'i var). Kullanilan istek: {budget.used}", level="ok")
    if saturated_cells:
        log(f"{saturated_cells} hucre doygun geldi — o bolgelerde sonuclar kesilmis olabilir. "
            f"--tomtom-cell-radius kucultun veya --tomtom-max-pages artirin.", level="warn")
    return results_list


def _best_match(results: Sequence[Dict[str, Any]], name: str,
                threshold: float) -> Tuple[Optional[Dict[str, Any]], float]:
    """Isim benzerligi esigi gecen en iyi POI."""
    best, best_score = None, 0.0
    for result in results:
        poi_name = (result.get("poi") or {}).get("name") or ""
        score = name_similarity(name, poi_name)
        if score >= threshold and score > best_score:
            best, best_score = result, score
    return best, best_score


def _enrich_from_poi(candidate: Candidate, match: Dict[str, Any]) -> None:
    """Eslesen TomTom kaydindan telefon/adres/url bilgisini kayda tasir."""
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
    """Tek aday icin TomTom POI aramasi. Yanit alinamazsa None, bos sonucta []."""
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
            "language": "tr-TR",
        },
    )
    if resp is None:
        return None
    try:
        return resp.json().get("results", [])
    except ValueError:
        return []


def _verify_one(http: HttpClient, cfg: Config, candidate: Candidate, budget: Budget) -> None:
    """Tek adayi TomTom'a sorar ve eslesirse verisiyle zenginlestirir."""
    if not candidate.name:
        candidate.add_note("TomTom atlandi (isimsiz)")
        return
    if not budget.take():
        candidate.add_note("TomTom kotasi doldu, dogrulanmadi")
        return

    results = _query_poi(http, cfg, candidate)
    if results is None:
        candidate.add_note("TomTom yanit vermedi")
        return

    candidate.tomtom_checked = True
    match, score = _best_match(results, candidate.name, cfg.name_threshold)
    if match is not None:
        _enrich_from_poi(candidate, match)
        candidate.add_note(f"TomTom'da eslesti (benzerlik {score:.2f})")


def verify_with_tomtom(http: HttpClient, cfg: Config, candidates: List[Candidate],
                       budget: Budget) -> List[Candidate]:
    """`verify` modu: adaylari tek tek sorgular, TomTom verisiyle zenginlestirir.

    Website tespiti burada YAPILMAZ — kayitlar sadece zenginlestirilir; ayirma
    isini pipeline tek yerde (split_by_website) yapar, boylece "website var mi"
    karari tek bir noktada kalir.
    """
    log(f"KATMAN 2 — TomTom dogrulama/verify ({len(candidates)} aday, "
        f"kota kalan: {budget.left})")

    for index, candidate in enumerate(candidates, 1):
        _verify_one(http, cfg, candidate, budget)
        if index % 25 == 0:
            log(f"  ... {index}/{len(candidates)} islendi ({budget.used} istek)", level="dbg")

    log(f"KATMAN 2 tamamlandi. Kullanilan istek: {budget.used}", level="ok")
    return candidates


def _probe_one_type(http: HttpClient, cfg: Config, btype: str,
                    center: Center, radius_m: int, sample: int) -> bool:
    """Tek tipi sorgulayip sonuclari basar. -> sonuc dondu mu (True/False)"""
    category = category_for(btype, cfg.tomtom_categories)
    url = TOMTOM_CATEGORY_SEARCH.format(query=requests.utils.quote(category, safe=""))
    resp = http.request(
        "GET", url, bucket=BUCKET, delay=cfg.delay_tomtom,
        params={"key": cfg.tomtom_key, "lat": f"{center.lat:.6f}",
                "lon": f"{center.lon:.6f}", "radius": radius_m,
                "limit": sample, "language": "tr-TR"},
    )
    print()
    print(f'--- {btype}   (arama metni: "{category}")')

    if resp is None:
        print("    ISTEK BASARISIZ")
        return False
    try:
        results = resp.json().get("results", [])
    except ValueError:
        results = []
    if not results:
        print("    !! SONUC YOK — arama metni bu bolgede calismiyor olabilir.")
        print(f'       Deneyin: --tomtom-category {btype}="<yerel terim>"')
        return False

    for result in results[:sample]:
        poi = result.get("poi") or {}
        cats = ", ".join(str(x) for x in (poi.get("categories") or []))
        has_site = "site VAR" if (poi.get("url") or "").strip() else "site yok"
        print(f"    - {(poi.get('name') or '?')[:38]:38s} [{cats[:26]:26s}] {has_site}")
    return True


def probe_categories(http: HttpClient, cfg: Config, types: Sequence[str],
                     center: Center, sample: int = 6) -> int:
    """Her is tipi icin TomTom'un ne dondurdugunu gosterir (kategori kalite kontrolu).

    Tip basina TEK istek harcar. Amac, production taramasi oncesi arama
    metinlerinin dogru sektoru getirdigini goz ile dogrulamak.
    """
    radius = min(cfg.radius_m, 5000)
    print("=" * 72)
    print(f"TOMTOM KATEGORI KONTROLU — {len(types)} tip, tip basina 1 istek")
    print(f"Merkez: {center.lat:.5f}, {center.lon:.5f}   Yaricap: {radius} m")
    print("=" * 72)

    suspicious = [t for t in types
                  if not _probe_one_type(http, cfg, t, center, radius, sample)]

    print()
    print("=" * 72)
    if suspicious:
        print("DIKKAT — su tipler dogrulanmali: " + ", ".join(suspicious))
        print("Duzeltmeden production taramasi yapmayin.")
    else:
        print("Tum tipler sonuc dondurdu. Yukaridaki isimlerin/kategorilerin")
        print("bekledigimiz sektore ait oldugunu goz ile teyit edin.")
    print("=" * 72)
    return 0
