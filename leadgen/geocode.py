# -*- coding: utf-8 -*-
"""Nominatim: adres -> koordinat (ve ters yonde semt adi).

Tek sorumluluk: geocoding. Nominatim politikasi geregi saniyede en fazla
1 istek atilir (gecikme Config'ten gelir) ve User-Agent iletisim bilgisi icerir.
"""

from __future__ import annotations

from typing import Optional

from .config import Config
from .console import log
from .http_client import HttpClient
from .models import Center

NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"

BUCKET = "nominatim"


def geocode_address(http: HttpClient, cfg: Config, address: str) -> Optional[Center]:
    """Adresi koordinata cevirir. Bulunamazsa None."""
    log(f"Adres cozumleniyor (Nominatim): {address}")
    resp = http.request(
        "GET", NOMINATIM_SEARCH,
        bucket=BUCKET, delay=cfg.delay_nominatim,
        params={"q": address, "format": "json", "limit": 1, "addressdetails": 1},
    )
    if resp is None:
        return None
    try:
        data = resp.json()
    except ValueError:
        log("Nominatim yaniti JSON degil.", level="err")
        return None
    if not data:
        log(f"Adres bulunamadi: {address}", level="err")
        return None

    item = data[0]
    center = Center(
        lat=float(item["lat"]),
        lon=float(item["lon"]),
        label=item.get("display_name", address),
    )
    log(f"Koordinat: {center.lat:.6f}, {center.lon:.6f}  ({center.label})", level="ok")
    return center


def reverse_city(http: HttpClient, cfg: Config, lat: float, lon: float) -> str:
    """Koordinattan semt/ilce adi — Katman 3 sorgusuna eklenir."""
    resp = http.request(
        "GET", NOMINATIM_REVERSE,
        bucket=BUCKET, delay=cfg.delay_nominatim,
        params={"lat": lat, "lon": lon, "format": "json", "zoom": 12, "addressdetails": 1},
    )
    if resp is None:
        return ""
    try:
        address = resp.json().get("address", {})
    except ValueError:
        return ""
    for key in ("suburb", "town", "city_district", "city", "county", "state"):
        if address.get(key):
            return str(address[key])
    return ""
