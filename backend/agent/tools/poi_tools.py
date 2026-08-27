"""agent/tools/poi_tools.py — search_poi + spatial_buffer.

Both tools sit on top of geo_data.SpatialIndex so they automatically benefit
from whichever backend (linear scan vs STRtree) the R-tree benchmark in
agent/spatial_index/benchmark.py recommends. Reuses the project's existing
study_rooms.geojson / campus_pois.geojson — same data the old REST endpoints
serve, per outline section 5's "复用" (reuse) requirement.
"""
from __future__ import annotations

from typing import Any, Literal

from agent.tools.registry import ToolSpec
from geo_data import PointRecord, SpatialIndex, load_point_records, point_in_campus

_INDEX_CACHE: dict[str, SpatialIndex] = {}


def _get_index(dataset: Literal["study_rooms", "pois", "both"], backend: str = "rtree") -> SpatialIndex:
    cache_key = f"{dataset}:{backend}"
    if cache_key not in _INDEX_CACHE:
        if dataset == "both":
            records = load_point_records("study_rooms") + load_point_records("pois")
        else:
            records = load_point_records(dataset)
        _INDEX_CACHE[cache_key] = SpatialIndex(records, backend=backend)  # type: ignore[arg-type]
    return _INDEX_CACHE[cache_key]


def _record_to_dict(rec: PointRecord, distance_m: float) -> dict[str, Any]:
    return {
        "id": rec.id,
        "name": rec.name,
        "dataset": rec.dataset,
        "lat": rec.lat,
        "lon": rec.lon,
        "distance_m": round(distance_m, 1),
        "properties": rec.properties,
    }


def _matches_filter(rec: PointRecord, category: str, extra_filter: dict[str, Any] | None) -> bool:
    if category:
        prop_value = rec.properties.get("category") or rec.properties.get("type")
        if prop_value != category:
            return False
    if extra_filter:
        for key, expected in extra_filter.items():
            if rec.properties.get(key) != expected:
                return False
    return True


async def search_poi(
    center: dict[str, float],
    category: str = "",
    radius: float = 800,
    dataset: Literal["study_rooms", "pois", "both"] = "both",
    limit: int = 10,
) -> dict[str, Any]:
    """Nearest-neighbour / category search around a point."""
    lat, lon = float(center["lat"]), float(center["lon"])
    index = _get_index(dataset)
    hits = index.query_radius(lat, lon, radius_m=radius)
    filtered = [(rec, d) for rec, d in hits if _matches_filter(rec, category, None)]
    limited = filtered[:limit]

    return {
        "query": {"center": {"lat": lat, "lon": lon}, "category": category or None, "radius_m": radius, "dataset": dataset},
        "count": len(limited),
        "total_within_radius": len(filtered),
        "results": [_record_to_dict(rec, d) for rec, d in limited],
        "center_in_campus": point_in_campus(lon, lat),
    }


async def spatial_buffer(
    center: dict[str, float],
    radius: float,
    dataset: Literal["study_rooms", "pois", "both"] = "both",
    filter: dict[str, Any] | None = None,  # noqa: A002 - matches outline's own tool signature name
    backend: Literal["linear", "rtree"] = "rtree",
) -> dict[str, Any]:
    """Buffer query: every feature within `radius` metres of `center`,
    optionally filtered by exact property match (e.g. {"noise_level": "quiet"}).
    `backend` is exposed mainly so the benchmark script and the Skill demo can
    force linear-scan for comparison; production callers should leave it at
    the default ("rtree"), which the section-12 benchmark justified.
    """
    lat, lon = float(center["lat"]), float(center["lon"])
    index = _get_index(dataset, backend=backend)
    hits = index.query_radius(lat, lon, radius_m=radius)
    filtered = [(rec, d) for rec, d in hits if _matches_filter(rec, "", filter)]

    return {
        "query": {"center": {"lat": lat, "lon": lon}, "radius_m": radius, "dataset": dataset, "filter": filter, "backend": backend},
        "count": len(filtered),
        "results": [_record_to_dict(rec, d) for rec, d in filtered],
        "center_in_campus": point_in_campus(lon, lat),
    }


SEARCH_POI_SPEC = ToolSpec(
    name="search_poi",
    description=(
        "在紫金港校区范围内，按类别搜索指定坐标附近的自习室或校园POI（图书馆、食堂、教学楼等），"
        "按距离升序返回。用于“帮我找离我最近的XX”这类请求。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "center": {
                "type": "object",
                "description": "查询中心点（WGS-84）。若用户给的是地名而非坐标，请先调用 geocode 工具。",
                "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}},
                "required": ["lat", "lon"],
            },
            "category": {
                "type": "string",
                "description": "可选类别过滤，如自习室的 quiet/discussion/overnight，或POI的 library/teaching/canteen/scenic/service/museum/other。留空表示不限类别。",
            },
            "radius": {"type": "number", "description": "搜索半径（米），默认800", "default": 800},
            "dataset": {"type": "string", "enum": ["study_rooms", "pois", "both"], "default": "both"},
            "limit": {"type": "integer", "description": "返回结果数量上限", "default": 10},
        },
        "required": ["center"],
    },
    handler=search_poi,
)

SPATIAL_BUFFER_SPEC = ToolSpec(
    name="spatial_buffer",
    description=(
        "缓冲区分析：返回指定坐标 radius 米范围内的全部点位（可选按属性精确过滤，如 noise_level=quiet）。"
        "与 search_poi 的区别：spatial_buffer 返回范围内全部结果用于统计/综合分析，search_poi 是限定数量的最近邻检索。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "center": {
                "type": "object",
                "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}},
                "required": ["lat", "lon"],
            },
            "radius": {"type": "number", "description": "缓冲区半径（米）"},
            "dataset": {"type": "string", "enum": ["study_rooms", "pois", "both"], "default": "both"},
            "filter": {
                "type": "object",
                "description": "可选的属性精确匹配过滤条件，例如 {\"noise_level\": \"quiet\"} 或 {\"category\": \"canteen\"}",
            },
        },
        "required": ["center", "radius"],
    },
    handler=spatial_buffer,
)
