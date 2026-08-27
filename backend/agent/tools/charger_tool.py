"""agent/tools/charger_tool.py — query_charging_pile(location).

Thin wrapper around charger_data.fetch_zju_charger_stations() (the same
function backing GET /api/chargers/stations) that adds the location-based
"nearest N chargers" behaviour the outline's tool table calls for. All of
the external-API-unavailable fallback behaviour (charger_unavailable /
fallback_url) is inherited unchanged from the original project.
"""
from __future__ import annotations

from typing import Any

from agent.tools.registry import ToolSpec
from charger_data import fetch_zju_charger_stations
from geo_data import haversine_m


async def query_charging_pile(location: dict[str, float] | None = None, radius: float = 0, limit: int = 5) -> dict[str, Any]:
    payload = await fetch_zju_charger_stations()

    if not payload.get("ok"):
        return {
            "ok": False,
            "message": payload.get("message", "充电桩数据不可用"),
            "fallback_url": payload.get("fallback_url"),
            "stations": [],
            "_degraded": True,
            "_degraded_reason": payload.get("message", "ZJU-Charger API 不可用"),
        }

    stations = payload.get("stations", [])

    if location is not None:
        lat, lon = float(location["lat"]), float(location["lon"])
        scored = []
        for st in stations:
            st_lat, st_lon = st.get("latitude"), st.get("longitude")
            if st_lat is None or st_lon is None:
                continue
            d = haversine_m(lat, lon, float(st_lat), float(st_lon))
            if radius and d > radius:
                continue
            scored.append((d, st))
        scored.sort(key=lambda x: x[0])
        stations = [{**st, "distance_m": round(d, 1)} for d, st in scored[:limit]]
    else:
        stations = stations[:limit]

    return {
        "ok": True,
        "message": payload.get("message"),
        "count": len(stations),
        "stations": stations,
    }


SPEC = ToolSpec(
    name="query_charging_pile",
    description=(
        "查询紫金港校区充电桩站点实时状态（通过 ZJU-Charger API 代理）。"
        "可选传入 location 按距离排序返回最近的站点；不传则返回全部已知站点。"
        "该接口不可用时会返回 ok=false 及外链兜底地址，而不是报错。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "location": {
                "type": "object",
                "description": "可选，查询中心点，用于按距离排序",
                "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}},
            },
            "radius": {"type": "number", "description": "可选，若提供 location，则只返回该半径（米）内的站点", "default": 0},
            "limit": {"type": "integer", "default": 5},
        },
        "required": [],
    },
    handler=query_charging_pile,
    degradation_notes="ZJU-Charger API unreachable/unconfigured -> ok=false + fallback_url (unchanged from original project).",
)
