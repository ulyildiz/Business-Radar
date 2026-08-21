# -*- coding: utf-8 -*-
"""Nominatim: address -> coordinates (and the reverse, for a locality name).

Single responsibility: geocoding. Nominatim's usage policy allows at most one
request per second (the delay comes from Config) and requires a descriptive
User-Agent carrying contact information.
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
    """Resolve an address to coordinates. Returns None if it cannot be found."""
    log(f"Resolving address (Nominatim): {address}")
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
        log("Nominatim response was not JSON.", level="err")
        return None
    if not data:
        log(f"Address not found: {address}", level="err")
        return None

    item = data[0]
    center = Center(
        lat=float(item["lat"]),
        lon=float(item["lon"]),
        label=item.get("display_name", address),
    )
    log(f"Coordinates: {center.lat:.6f}, {center.lon:.6f}  ({center.label})", level="ok")
    return center


def reverse_city(http: HttpClient, cfg: Config, lat: float, lon: float) -> str:
    """Locality name for a coordinate — appended to the Layer 3 query."""
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
