"""geo_data.py — Shared geospatial data-access + spatial-index helpers.

This module is the single source of truth for reading the project's GeoJSON
datasets and for doing point-distance / nearest-neighbour math. It was
extracted out of main.py (where it used to be duplicated with what the agent
layer needed) so that:

  * the existing REST endpoints in main.py, and
  * the new GeoAgent tool layer in agent/tools/*

both call the *same* functions instead of maintaining two copies of Haversine
math and GeoJSON parsing. See docs/AGENT_ARCHITECTURE.md section "Data
structures" for why `SpatialIndex` offers both a linear-scan and an STRtree
backend (this is the real experiment described in the project outline, not
just an abstraction for its own sake — see agent/spatial_index/benchmark.py).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any, Literal

from shapely.geometry import Point
from shapely.strtree import STRtree

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"

EMPTY_FEATURE_COLLECTION: dict = {"type": "FeatureCollection", "features": []}

DATASET_FILENAMES = {
    "study_rooms": "study_rooms.geojson",
    "pois": "campus_pois.geojson",
    "buildings": "buildings.geojson",
    "zjg_boundary": "zjg.geojson",
}


# ── GeoJSON IO ──────────────────────────────────────────────────────────────

def read_geojson(filename: str) -> dict:
    """Read a GeoJSON FeatureCollection from DATA_DIR, or an empty one on any
    problem (missing file, bad JSON, wrong type). Never raises — this mirrors
    the project's existing "never blank-screen the user" empty-state policy.
    """
    path = DATA_DIR / filename
    if not path.exists():
        return EMPTY_FEATURE_COLLECTION

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return EMPTY_FEATURE_COLLECTION

    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        return EMPTY_FEATURE_COLLECTION

    return data


def read_dataset(name: Literal["study_rooms", "pois", "buildings", "zjg_boundary"]) -> dict:
    return read_geojson(DATASET_FILENAMES[name])


def safe_properties(feature: dict) -> dict:
    properties = feature.get("properties")
    return properties if isinstance(properties, dict) else {}


def point_coords(feature: dict) -> tuple[float, float] | None:
    """Return (lon, lat) from a GeoJSON Point feature, or None."""
    coords = feature.get("geometry", {}).get("coordinates", [])
    if len(coords) < 2:
        return None
    try:
        return float(coords[0]), float(coords[1])
    except (TypeError, ValueError):
        return None


# ── Spherical distance ──────────────────────────────────────────────────────

EARTH_RADIUS_M = 6_371_000


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS-84 points."""
    phi1, phi2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


# ── Campus bounding box (used by the agent's result-validation step) ───────

def campus_bbox(margin_deg: float = 0.01) -> tuple[float, float, float, float]:
    """(min_lon, min_lat, max_lon, max_lat) derived from the real zjg.geojson
    boundary polygon, expanded by a small margin. The Orchestrator's
    VALIDATING state uses this to sanity-check tool results ("is this
    coordinate actually inside campus?"), per outline section 4 step 4.
    """
    boundary = read_dataset("zjg_boundary")
    lons: list[float] = []
    lats: list[float] = []

    def walk(coords: Any) -> None:
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            lons.append(coords[0])
            lats.append(coords[1])
            return
        for c in coords:
            walk(c)

    for feature in boundary.get("features", []):
        walk(feature.get("geometry", {}).get("coordinates"))

    if not lons or not lats:
        # Fallback: rough Zijingang campus envelope (WGS-84) if boundary is empty.
        return (120.06, 30.29, 120.10, 30.32)

    return (
        min(lons) - margin_deg,
        min(lats) - margin_deg,
        max(lons) + margin_deg,
        max(lats) + margin_deg,
    )


def point_in_campus(lon: float, lat: float) -> bool:
    min_lon, min_lat, max_lon, max_lat = campus_bbox()
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


# ── Uniform point records (used by tools, skills, benchmark) ───────────────

@dataclass
class PointRecord:
    id: str
    name: str
    lon: float
    lat: float
    dataset: str
    properties: dict = field(default_factory=dict)


def load_point_records(dataset: Literal["study_rooms", "pois"]) -> list[PointRecord]:
    collection = read_dataset(dataset)
    records: list[PointRecord] = []
    for feature in collection.get("features", []):
        coords = point_coords(feature)
        if coords is None:
            continue
        lon, lat = coords
        props = safe_properties(feature)
        records.append(
            PointRecord(
                id=str(props.get("id") or f"{dataset}_{len(records)}"),
                name=str(props.get("name") or "未知点位"),
                lon=lon,
                lat=lat,
                dataset=dataset,
                properties=props,
            )
        )
    return records


# ── Spatial index: linear scan vs STRtree (real R-tree family index) ───────
#
# `SpatialIndex` wraps two interchangeable backends behind the same query
# surface. The linear-scan backend is literally the same Haversine loop the
# project shipped originally. The "rtree" backend uses Shapely's STRtree
# (packed R-tree variant) — the project outline explicitly allows STRtree as
# an alternative to the `rtree` package, since STRtree avoids the libspatialindex
# system dependency. Both are exercised for real in
# agent/spatial_index/benchmark.py to produce the "why we switched" numbers.

class SpatialIndex:
    def __init__(self, records: list[PointRecord], backend: Literal["linear", "rtree"] = "rtree"):
        self.records = records
        self.backend = backend
        self._tree: STRtree | None = None
        self._geoms: list[Point] = []
        if backend == "rtree" and records:
            self._geoms = [Point(r.lon, r.lat) for r in records]
            self._tree = STRtree(self._geoms)

    def __len__(self) -> int:
        return len(self.records)

    def query_radius(self, lat: float, lon: float, radius_m: float) -> list[tuple[PointRecord, float]]:
        """All records within radius_m metres, sorted by distance ascending."""
        if self.backend == "linear" or self._tree is None:
            return self._query_radius_linear(lat, lon, radius_m)
        return self._query_radius_rtree(lat, lon, radius_m)

    def query_nearest(self, lat: float, lon: float, k: int) -> list[tuple[PointRecord, float]]:
        """The k nearest records, sorted by distance ascending."""
        if self.backend == "linear" or self._tree is None:
            return self._query_nearest_linear(lat, lon, k)
        return self._query_nearest_rtree(lat, lon, k)

    # -- linear scan (baseline / fallback) --------------------------------

    def _query_radius_linear(self, lat, lon, radius_m):
        out = []
        for rec in self.records:
            d = haversine_m(lat, lon, rec.lat, rec.lon)
            if d <= radius_m:
                out.append((rec, d))
        out.sort(key=lambda x: (x[1], x[0].id))
        return out

    def _query_nearest_linear(self, lat, lon, k):
        scored = [(rec, haversine_m(lat, lon, rec.lat, rec.lon)) for rec in self.records]
        scored.sort(key=lambda x: (x[1], x[0].id))
        return scored[:k]

    # -- STRtree (packed R-tree) -------------------------------------------
    #
    # STRtree indexes in *planar* (lon, lat) degree-space, so we first ask it
    # for a cheap candidate set using a degree-space buffer around the query
    # point (fast, approximate), then re-rank that (small) candidate set with
    # exact Haversine distance. This is the standard "index for candidates,
    # exact-math for ranking" pattern used with all planar spatial indexes on
    # geographic data.

    _DEG_PER_M = 1 / 111_320  # ~ metres per degree of latitude, good enough for a coarse pre-filter

    def _query_radius_rtree(self, lat, lon, radius_m):
        assert self._tree is not None
        deg_radius = radius_m * self._DEG_PER_M * 1.5  # 1.5x safety margin for longitude compression
        query_geom = Point(lon, lat).buffer(deg_radius)
        idx = self._tree.query(query_geom)
        out = []
        for i in idx:
            rec = self.records[int(i)]
            d = haversine_m(lat, lon, rec.lat, rec.lon)
            if d <= radius_m:
                out.append((rec, d))
        out.sort(key=lambda x: (x[1], x[0].id))
        return out

    def _query_nearest_rtree(self, lat, lon, k):
        assert self._tree is not None
        # Expand the search radius until we have >= k candidates (starts small,
        # so this stays fast even for "nearest 5 of 10,000" on a dense set).
        deg_radius = 300 * self._DEG_PER_M
        for _ in range(8):
            query_geom = Point(lon, lat).buffer(deg_radius)
            idx = self._tree.query(query_geom)
            if len(idx) >= k or len(idx) >= len(self.records):
                break
            deg_radius *= 3
        scored = [(self.records[int(i)], haversine_m(lat, lon, self.records[int(i)].lat, self.records[int(i)].lon)) for i in idx]
        scored.sort(key=lambda x: (x[1], x[0].id))
        return scored[:k]


def timed_query(fn, *args, repeats: int = 1, **kwargs):
    """Run fn(*args, **kwargs) `repeats` times, return (result, mean_seconds)."""
    start = time.perf_counter()
    result = None
    for _ in range(repeats):
        result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - start
    return result, elapsed / repeats
