# -*- coding: utf-8 -*-
"""KATMAN 1a — OpenStreetMap / Overpass kesfi.

Tek sorumluluk: OSM ile konusmak ve donen ogeleri `Candidate`'a cevirmek.
CSV yazmaz, baska API cagirmaz.

Not: bu modul website filtresi UYGULAMAZ. Filtreleme, OSM ve TomTom
sonuclari birlestirildikten sonra yapilir (bkz. merge.py / pipeline.py);
aksi halde bir kaynagin eksik verisi digerinin bilgisini golgelerdi.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .config import Config
from .console import log
from .geo_utils import haversine_m
from .http_client import HttpClient
from .models import SOURCE_OSM, Candidate, Center
from .text_utils import is_real_website, norm_text

OVERPASS_BUCKET = "overpass"

# ---------------------------------------------------------------------------
# Is tipi -> OSM tag eslemesi (genisletilebilir)
# Bir tip birden fazla tag'e karsilik gelebilir.
# ---------------------------------------------------------------------------
BUSINESS_TYPES: Dict[str, List[Tuple[str, str]]] = {
    # yeme-icme
    "restaurant":      [("amenity", "restaurant")],
    "cafe":            [("amenity", "cafe")],
    "bar":             [("amenity", "bar"), ("amenity", "pub")],
    "fast_food":       [("amenity", "fast_food")],
    "bakery":          [("shop", "bakery")],
    "patisserie":      [("shop", "pastry"), ("shop", "confectionery")],
    "butcher":         [("shop", "butcher")],
    "greengrocer":     [("shop", "greengrocer")],
    "supermarket":     [("shop", "supermarket"), ("shop", "convenience")],
    # kisisel bakim
    "hair_salon":      [("shop", "hairdresser")],
    "beauty_salon":    [("shop", "beauty")],
    "spa":             [("leisure", "spa"), ("shop", "massage")],
    "tattoo":          [("shop", "tattoo")],
    # oto
    "car_repair":      [("shop", "car_repair"), ("craft", "car_repair")],
    "car_dealer":      [("shop", "car")],
    "car_wash":        [("amenity", "car_wash")],
    "car_parts":       [("shop", "car_parts")],
    "tyres":           [("shop", "tyres")],
    "motorcycle":      [("shop", "motorcycle"), ("shop", "motorcycle_repair")],
    # saglik
    "dentist":         [("amenity", "dentist")],
    "doctor":          [("amenity", "doctors")],
    "pharmacy":        [("amenity", "pharmacy")],
    "veterinary":      [("amenity", "veterinary")],
    "physiotherapist": [("healthcare", "physiotherapist")],
    "optician":        [("shop", "optician")],
    # profesyonel hizmet / ofis
    "lawyer":          [("office", "lawyer")],
    "accountant":      [("office", "accountant"), ("office", "tax_advisor")],
    "estate_agent":    [("office", "estate_agent")],
    "insurance":       [("office", "insurance")],
    "travel_agency":   [("shop", "travel_agency"), ("office", "travel_agent")],
    "architect":       [("office", "architect")],
    "advertising":     [("office", "advertising_agency")],
    # zanaat / teknik
    "plumber":         [("craft", "plumber")],
    "electrician":     [("craft", "electrician")],
    "carpenter":       [("craft", "carpenter")],
    "painter":         [("craft", "painter")],
    "locksmith":       [("craft", "locksmith"), ("shop", "locksmith")],
    "hvac":            [("craft", "hvac")],
    "photographer":    [("craft", "photographer"), ("shop", "photo")],
    # spor / egitim / diger
    "gym":             [("leisure", "fitness_centre")],
    "sports_centre":   [("leisure", "sports_centre")],
    "driving_school":  [("amenity", "driving_school")],
    "language_school": [("amenity", "language_school")],
    "kindergarten":    [("amenity", "kindergarten")],
    "hotel":           [("tourism", "hotel"), ("tourism", "guest_house")],
    "florist":         [("shop", "florist")],
    "jewelry":         [("shop", "jewelry")],
    "clothes":         [("shop", "clothes")],
    "shoes":           [("shop", "shoes")],
    "furniture":       [("shop", "furniture")],
    "hardware":        [("shop", "hardware"), ("shop", "doityourself")],
    "computer":        [("shop", "computer")],
    "mobile_phone":    [("shop", "mobile_phone")],
    "laundry":         [("shop", "laundry"), ("shop", "dry_cleaning")],
    "pet_shop":        [("shop", "pet")],
    "bookshop":        [("shop", "books")],
    "bicycle":         [("shop", "bicycle")],
    "copyshop":        [("shop", "copyshop")],
    "funeral":         [("shop", "funeral_directors")],
}

# Turkce (ve bazi ingilizce) takma adlar -> kanonik tip
TYPE_ALIASES: Dict[str, str] = {
    "restoran": "restaurant", "lokanta": "restaurant", "yemek": "restaurant",
    "kafe": "cafe", "kahve": "cafe", "coffee": "cafe", "kahveci": "cafe",
    "pub": "bar", "meyhane": "bar",
    "fastfood": "fast_food", "bufe": "fast_food",
    "firin": "bakery", "ekmek": "bakery",
    "pastane": "patisserie", "tatlici": "patisserie",
    "kasap": "butcher", "manav": "greengrocer", "market": "supermarket",
    "bakkal": "supermarket",
    "kuafor": "hair_salon", "berber": "hair_salon", "hairdresser": "hair_salon",
    "guzellik": "beauty_salon", "guzellik_salonu": "beauty_salon",
    "dovme": "tattoo", "masaj": "spa",
    "oto_tamirci": "car_repair", "tamirci": "car_repair", "sanayi": "car_repair",
    "oto": "car_repair", "otoservis": "car_repair", "oto_servis": "car_repair",
    "galeri": "car_dealer", "oto_yikama": "car_wash", "yedek_parca": "car_parts",
    "lastikci": "tyres", "motosiklet": "motorcycle",
    "dis": "dentist", "disci": "dentist", "dis_hekimi": "dentist",
    "doktor": "doctor", "hekim": "doctor", "muayenehane": "doctor",
    "eczane": "pharmacy", "veteriner": "veterinary",
    "fizyoterapist": "physiotherapist", "gozluk": "optician", "gozlukcu": "optician",
    "avukat": "lawyer", "hukuk": "lawyer",
    "muhasebeci": "accountant", "mali_musavir": "accountant", "smmm": "accountant",
    "emlak": "estate_agent", "emlakci": "estate_agent",
    "sigorta": "insurance", "acente": "insurance",
    "turizm": "travel_agency", "seyahat": "travel_agency",
    "mimar": "architect", "reklam": "advertising", "ajans": "advertising",
    "tesisatci": "plumber", "su_tesisati": "plumber",
    "elektrikci": "electrician", "marangoz": "carpenter", "dogramaci": "carpenter",
    "boyaci": "painter", "cilingir": "locksmith",
    "klima": "hvac", "kombi": "hvac", "dogalgaz": "hvac",
    "fotografci": "photographer",
    "spor_salonu": "gym", "fitness": "gym", "spor": "gym",
    "surucu_kursu": "driving_school", "dil_kursu": "language_school",
    "kres": "kindergarten", "anaokulu": "kindergarten",
    "otel": "hotel", "pansiyon": "hotel",
    "cicekci": "florist", "kuyumcu": "jewelry",
    "giyim": "clothes", "butik": "clothes", "ayakkabi": "shoes",
    "mobilya": "furniture", "hirdavat": "hardware", "nalbur": "hardware",
    "bilgisayar": "computer", "telefon": "mobile_phone", "teknoloji": "computer",
    "kuru_temizleme": "laundry", "camasirhane": "laundry",
    "petshop": "pet_shop", "kitapci": "bookshop", "bisiklet": "bicycle",
    "kirtasiye": "copyshop", "matbaa": "copyshop",
    "cenaze": "funeral",
}

# OSM'de "web sitesi var" sayilan tag'ler
WEBSITE_TAGS = (
    "website", "contact:website", "url", "contact:url",
    "website:en", "website:tr", "official_website",
)

NAME_TAGS = ("name", "name:tr", "official_name", "brand", "operator")
PHONE_TAGS = ("phone", "contact:phone", "contact:mobile", "mobile")
EMAIL_TAGS = ("email", "contact:email")
SOCIAL_TAGS = ("contact:instagram", "contact:facebook", "facebook", "instagram")


class UnknownBusinessType(Exception):
    """Kullanici tanimsiz bir is tipi verdi."""

    def __init__(self, unknown: Sequence[str]):
        self.unknown = list(unknown)
        super().__init__(", ".join(unknown))


def resolve_types(raw_types: Sequence[str]) -> List[str]:
    """Kullanici girdisini kanonik tip adlarina cevirir ('restoran' -> 'restaurant')."""
    resolved: List[str] = []
    unknown: List[str] = []
    for raw in raw_types:
        key = norm_text(raw).replace(" ", "_")
        if key == "all":
            return sorted(BUSINESS_TYPES)
        canonical = key if key in BUSINESS_TYPES else TYPE_ALIASES.get(key)
        if canonical is None:
            unknown.append(raw)
        elif canonical not in resolved:
            resolved.append(canonical)
    if unknown:
        raise UnknownBusinessType(unknown)
    return resolved


def build_overpass_query(types: Sequence[str], lat: float, lon: float,
                         radius_m: int, timeout_s: int) -> str:
    """Tek sorguda tum yaricapi kapsayan Overpass QL uretir (tiling gerekmez)."""
    lines = [f"[out:json][timeout:{timeout_s}];", "("]
    for btype in types:
        for key, value in BUSINESS_TYPES[btype]:
            lines.append(f'  nwr["{key}"="{value}"](around:{radius_m},{lat:.6f},{lon:.6f});')
    lines.append(");")
    lines.append("out center tags;")
    return "\n".join(lines)


def _first_tag(tags: Dict[str, str], keys: Sequence[str]) -> str:
    for key in keys:
        value = (tags.get(key) or "").strip()
        if value:
            return value
    return ""


def _build_address(tags: Dict[str, str]) -> str:
    """OSM addr:* tag'lerinden okunabilir tek satirlik adres kurar."""
    street = _first_tag(tags, ("addr:street",))
    number = _first_tag(tags, ("addr:housenumber",))
    quarter = _first_tag(tags, ("addr:neighbourhood", "addr:suburb", "addr:quarter"))
    city = _first_tag(tags, ("addr:city", "addr:town", "addr:village", "addr:district"))
    postcode = _first_tag(tags, ("addr:postcode",))
    line1 = " ".join(x for x in (street, f"No:{number}" if number else "") if x).strip()
    return ", ".join(p for p in (line1, quarter, postcode, city) if p)


def _classify_type(tags: Dict[str, str], wanted: Sequence[str]) -> Tuple[str, str]:
    """Ogenin hangi is tipine dustugunu bulur -> (tip, 'anahtar=deger')."""
    for btype in wanted:
        for key, value in BUSINESS_TYPES[btype]:
            if tags.get(key) == value:
                return btype, f"{key}={value}"
    for key in ("amenity", "shop", "craft", "office", "leisure", "tourism", "healthcare", "club"):
        if tags.get(key):
            return tags[key], f"{key}={tags[key]}"
    return "unknown", ""


def _element_position(element: Dict) -> Tuple[Optional[float], Optional[float]]:
    """node icin lat/lon, way/relation icin center."""
    if element.get("type") == "node":
        return element.get("lat"), element.get("lon")
    center = element.get("center") or {}
    return center.get("lat"), center.get("lon")


def _element_to_candidate(element: Dict, types: Sequence[str],
                          center: Center, radius_m: int) -> Optional[Candidate]:
    """Tek bir Overpass ogesini Candidate'a cevirir; uygun degilse None."""
    tags = element.get("tags") or {}
    lat, lon = _element_position(element)
    if lat is None or lon is None:
        return None

    distance = haversine_m(center.lat, center.lon, float(lat), float(lon))
    if distance > radius_m:
        return None

    biz_type, matched_tag = _classify_type(tags, types)
    element_type = element.get("type", "node")
    return Candidate(
        name=_first_tag(tags, NAME_TAGS),
        biz_type=biz_type,
        lat=float(lat),
        lon=float(lon),
        distance_m=int(round(distance)),
        address=_build_address(tags),
        phone=_first_tag(tags, PHONE_TAGS),
        email=_first_tag(tags, EMAIL_TAGS),
        opening_hours=tags.get("opening_hours", ""),
        social=_first_tag(tags, SOCIAL_TAGS),
        osm_type=element_type,
        osm_id=int(element.get("id", 0)),
        osm_tags_matched=matched_tag,
        osm_link=f"https://www.openstreetmap.org/{element_type}/{element.get('id')}",
        osm_website=_first_tag(tags, WEBSITE_TAGS),
        sources=[SOURCE_OSM],
    )


def _richer(a: Candidate, b: Candidate) -> Candidate:
    """Ayni isletmenin iki kopyasindan daha dolu olani secer."""
    score = lambda c: sum(bool(x) for x in (c.phone, c.address, c.email, c.osm_website))
    return a if score(a) >= score(b) else b


def _fetch_overpass(http: HttpClient, cfg: Config, query: str) -> Optional[Dict]:
    """Mirror'lari sirayla dener; ilk basarili JSON yaniti doner."""
    for mirror in cfg.overpass_mirrors:
        log(f"Overpass mirror deneniyor: {mirror}")
        resp = http.request(
            "POST", mirror,
            bucket=OVERPASS_BUCKET, delay=cfg.delay_overpass,
            data={"data": query}, timeout=cfg.overpass_timeout + 30,
        )
        if resp is None:
            continue
        try:
            return resp.json()
        except ValueError:
            log("Overpass yaniti JSON degil, sonraki mirror deneniyor.", level="warn")
    return None


def search_osm(http: HttpClient, cfg: Config, types: Sequence[str], center: Center) -> List[Candidate]:
    """Bolgedeki TUM isletmeleri dondurur (website filtresi uygulanmaz)."""
    query = build_overpass_query(types, center.lat, center.lon, cfg.radius_m, cfg.overpass_timeout)
    log(f"KATMAN 1a — Overpass sorgusu ({len(types)} tip, {cfg.radius_m} m yaricap)")
    log(f"Sorgu:\n{query}", level="dbg")

    data = _fetch_overpass(http, cfg, query)
    if data is None:
        log("Tum Overpass mirror'lari basarisiz oldu.", level="err")
        return []

    elements = data.get("elements", [])
    log(f"Overpass {len(elements)} ham oge dondurdu.", level="ok")

    unique: Dict[str, Candidate] = {}
    skipped_unnamed = 0
    skipped_far = 0

    for element in elements:
        candidate = _element_to_candidate(element, types, center, cfg.radius_m)
        if candidate is None:
            skipped_far += 1
            continue
        if not candidate.name and not cfg.include_unnamed:
            skipped_unnamed += 1
            continue
        existing = unique.get(candidate.dedup_key)
        unique[candidate.dedup_key] = candidate if existing is None else _richer(existing, candidate)

    results = sorted(unique.values(), key=lambda c: c.distance_m)
    with_site = sum(1 for c in results if c.osm_website and is_real_website(c.osm_website))
    log(f"KATMAN 1a (OSM) sonucu: {len(results)} isletme "
        f"({with_site} tanesinin OSM'de website tag'i var), "
        f"{skipped_unnamed} isimsiz atlandi, {skipped_far} yaricap disi atildi.", level="ok")
    return results
