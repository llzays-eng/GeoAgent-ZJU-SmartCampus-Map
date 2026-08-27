"""agent/tools/geocode_tool.py — geocode(address).

Call chain, in order:

  1. GEOCODE_BACKEND=mcp (opt-in, default is "inprocess"): try the Node MCP
     geocode server via agent/tools/mcp_bridge.py's real stdio MCP client.
     Falls through to step 2 on any failure (mcp package missing, server
     not built, subprocess/protocol error) — see _geocode_mcp() below.
  2. AMap (高德地图) Web geocoding API, same provider the original project's
     "城市空间叙事" project used (per outline section 5's reuse table).
  3. A local gazetteer built at import time from the names already present
     in study_rooms.geojson / campus_pois.geojson — e.g. "基础图书馆" or "北
     教学区" resolve locally with zero network calls. This means the tool
     always returns *something* useful for on-campus queries even with no
     AMAP_API_KEY configured or no network access, mirroring the project's
     existing "never blank screen" fallback philosophy.

The local gazetteer also does simple substring / token matching so
"图书馆" or "紫金港东门" style partial names still resolve.
"""
from __future__ import annotations

import difflib
from functools import lru_cache
from typing import Any

import httpx

from agent.config import get_settings
from agent.tools.registry import ToolSpec
from geo_data import load_point_records


@lru_cache
def _local_gazetteer() -> dict[str, tuple[float, float]]:
    entries: dict[str, tuple[float, float]] = {}
    for dataset in ("study_rooms", "pois"):
        for rec in load_point_records(dataset):  # type: ignore[arg-type]
            entries.setdefault(rec.name, (rec.lat, rec.lon))
            building = rec.properties.get("building")
            if building:
                entries.setdefault(str(building), (rec.lat, rec.lon))
    return entries


def _match_gazetteer(address: str) -> tuple[str, float, float] | None:
    gaz = _local_gazetteer()
    if address in gaz:
        lat, lon = gaz[address]
        return address, lat, lon

    # substring match ("紫金港东门附近" contains no exact key, but tokens might)
    for name, (lat, lon) in gaz.items():
        if name in address or address in name:
            return name, lat, lon

    # fuzzy match as a last resort (handles typos / minor phrasing differences)
    close = difflib.get_close_matches(address, gaz.keys(), n=1, cutoff=0.55)
    if close:
        name = close[0]
        lat, lon = gaz[name]
        return name, lat, lon

    return None


async def _geocode_amap(address: str, api_key: str, base_url: str) -> dict[str, Any] | None:
    params = {"address": address, "key": api_key, "city": "杭州"}
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.get(base_url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None

    if data.get("status") != "1" or not data.get("geocodes"):
        return None

    geocode = data["geocodes"][0]
    location = geocode.get("location", "")
    try:
        lon_str, lat_str = location.split(",")
        lon, lat = float(lon_str), float(lat_str)
    except (ValueError, AttributeError):
        return None

    return {
        "matched_address": geocode.get("formatted_address", address),
        "lat": lat,
        "lon": lon,
        "confidence": "amap_exact" if geocode.get("level") in {"兴趣点", "门牌号"} else "amap_approx",
    }


async def _geocode_mcp(address: str) -> dict[str, Any] | None:
    """Try the Node MCP geocode server (GEOCODE_BACKEND=mcp). Returns None on
    ANY failure — `mcp` package not installed, server not built, subprocess/
    protocol error, or a well-formed-but-unsuccessful MCP response — so the
    caller falls through to the in-process AMap/gazetteer chain below. This
    is the "MCP backend unavailable, use the in-process tool instead"
    behaviour agent/tools/mcp_bridge.py's docstring already promised, now
    actually wired into the call path instead of only being reachable via
    that module's own __main__ smoke test."""
    try:
        from agent.tools.mcp_bridge import geocode_via_mcp

        result = await geocode_via_mcp(address)
    except Exception:
        return None

    if not isinstance(result, dict) or not result.get("ok"):
        return None
    try:
        return {
            "ok": True,
            "source": result.get("source", "mcp_geocode_server"),
            "query": address,
            "matched_address": result["matched_address"],
            "lat": float(result["lat"]),
            "lon": float(result["lon"]),
            "confidence": result.get("confidence", "mcp_match"),
        }
    except (KeyError, TypeError, ValueError):
        return None  # malformed MCP response -> treat as unavailable, don't crash the loop


async def geocode(address: str) -> dict[str, Any]:
    address = address.strip()
    if not address:
        return {"ok": False, "message": "地址不能为空", "results": []}

    settings = get_settings()

    if settings.geocode_backend == "mcp":
        mcp_result = await _geocode_mcp(address)
        if mcp_result:
            return mcp_result
        # MCP backend unavailable/failed for this call -> fall through to
        # the in-process chain below rather than returning an error; same
        # graceful-degradation philosophy as the AMap fallback right after.

    if settings.amap_api_key:
        amap_result = await _geocode_amap(address, settings.amap_api_key, settings.amap_base_url)
        if amap_result:
            return {
                "ok": True,
                "source": "amap",
                "query": address,
                **amap_result,
            }

    # Fallback: local gazetteer built from this project's own data.
    match = _match_gazetteer(address)
    if match:
        name, lat, lon = match
        return {
            "ok": True,
            "source": "local_gazetteer",
            "query": address,
            "matched_address": name,
            "lat": lat,
            "lon": lon,
            "confidence": "gazetteer_match",
            "_degraded": True,
            "_degraded_reason": (
                "未配置 AMAP_API_KEY 或高德接口不可用，已退化为基于项目自有POI/自习室数据构建的本地地名词典匹配。"
            ),
        }

    return {
        "ok": False,
        "source": "none",
        "query": address,
        "message": "未能解析该地址：高德API不可用/未配置，且本地地名词典（基于校园POI/自习室数据）中无匹配项。",
        "results": [],
    }


SPEC = ToolSpec(
    name="geocode",
    description=(
        "将地址/地名字符串解析为WGS-84经纬度坐标。GEOCODE_BACKEND=mcp 时优先经 MCP 协议调用 Node geocode 服务；"
        "否则/该调用失败时优先调用高德地图API；"
        "未配置Key或调用失败时，自动退化为基于校园POI/自习室数据构建的本地地名词典模糊匹配。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "address": {"type": "string", "description": "地址或地名，例如“紫金港校区基础图书馆”或“北教学区”"},
        },
        "required": ["address"],
    },
    handler=geocode,
    degradation_notes=(
        "GEOCODE_BACKEND=mcp but the MCP server is unreachable/unbuilt -> falls through to the in-process chain below. "
        "No AMAP_API_KEY / AMap unreachable -> local gazetteer built from project's own POI + study-room names."
    ),
)
