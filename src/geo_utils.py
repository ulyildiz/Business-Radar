# -*- coding: utf-8 -*-
"""Shared geographic helpers.

Single responsibility: coordinate math. Deliberately sits at the lowest
level — it does NOT import higher-level modules such as osm_source or
tomtom_source.
"""

from __future__ import annotations

import math
from typing import List, Tuple

EARTH_RADIUS_M = 6371008.8
METERS_PER_DEG_LAT = 111320.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two coordinates, in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def meters_to_deg(lat: float, meters: float) -> Tuple[float, float]:
    """Convert meters into a (latitude_degrees, longitude_degrees) delta."""
    dlat = meters / METERS_PER_DEG_LAT
    cos_lat = max(math.cos(math.radians(lat)), 1e-6)
    dlon = meters / (METERS_PER_DEG_LAT * cos_lat)
    return dlat, dlon


def build_grid(lat: float, lon: float, radius_m: float, cell_radius_m: float) -> List[Tuple[float, float]]:
    """Tile the search radius with cells of radius `cell_radius_m`.

    Cell centers are spaced cell_radius_m * sqrt(2) apart, so the square each
    center owns fits entirely inside that cell's circle and the scan leaves no
    gaps. Required for sources that cap results per call (TomTom returns at
    most 100); not needed for Overpass, which answers the whole radius at once.
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
            # Keep the cell if its circle can possibly intersect the search area.
            if haversine_m(lat, lon, clat, clon) <= radius_m + cell_radius_m:
                cells.append((clat, clon))
    return cells
