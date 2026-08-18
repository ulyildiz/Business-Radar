# -*- coding: utf-8 -*-
"""Paylasilan cografi yardimcilar.

Tek sorumluluk: koordinat matematigi. Bilerek en dusuk seviyede durur —
osm_source / tomtom_source gibi ust seviye modulleri IMPORT ETMEZ.
"""

from __future__ import annotations

import math
from typing import List, Tuple

EARTH_RADIUS_M = 6371008.8
METERS_PER_DEG_LAT = 111320.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Iki koordinat arasi buyuk daire mesafesi (metre)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def meters_to_deg(lat: float, meters: float) -> Tuple[float, float]:
    """Metreyi (enlem_derece, boylam_derece) farkina cevirir."""
    dlat = meters / METERS_PER_DEG_LAT
    cos_lat = max(math.cos(math.radians(lat)), 1e-6)
    dlon = meters / (METERS_PER_DEG_LAT * cos_lat)
    return dlat, dlon


def build_grid(lat: float, lon: float, radius_m: float, cell_radius_m: float) -> List[Tuple[float, float]]:
    """Yaricapi, her biri cell_radius_m yaricapinda hucrelerle kaplar.

    Hucre merkezleri cell_radius_m*sqrt(2) araliginda yerlestirilir; boylece
    her hucrenin cevreledigi kare tamamen hucre dairesinin icinde kalir ve
    taramada bosluk olusmaz. (Tek cagrida 100 sonuc siniri olan TomTom gibi
    kaynaklar icin gerekli; Overpass'ta gerekmez.)
    """
    if cell_radius_m <= 0 or cell_radius_m >= radius_m:
        return [(lat, lon)]

    step_m = cell_radius_m * math.sqrt(2)
    dlat, dlon = meters_to_deg(lat, step_m)
    steps = int(math.ceil(radius_m / step_m))

    cells: List[Tuple[float, float]] = []
    for i in range(-steps, steps + 1):
        for j in range(-steps, steps + 1):
            clat = lat + i * dlat
            clon = lon + j * dlon
            # hucrenin daireyle kesisme ihtimali varsa dahil et
            if haversine_m(lat, lon, clat, clon) <= radius_m + cell_radius_m:
                cells.append((clat, clon))
    return cells
