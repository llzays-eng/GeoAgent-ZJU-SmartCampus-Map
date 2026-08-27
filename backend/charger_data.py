"""charger_data.py — ZJU-Charger API access + normalization.

Extracted out of main.py for the same reason as geo_data.py: the new
GeoAgent `query_charging_pile` tool (agent/tools/charger_tool.py) needs the
exact same fetch/normalize logic the REST endpoint uses, and importing
main.py from the agent package would create a circular import (main.py
mounts the agent router).

Note: while extracting this, `normalize_charger_station` turned out to be
defined *twice* in the original main.py (Python silently kept the second
definition — a harmless but real pre-existing bug). Only the second,
more complete version (the one with to_int/to_float coercion and
has_available_ports) is kept here.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


def get_charger_site_url() -> str:
    return os.getenv(
        "ZJU_CHARGER_SITE_URL",
        os.getenv("CHARGER_URL", "https://charger.philfan.cn/"),
    )


def charger_unavailable(message: str) -> dict:
    return {
        "ok": False,
        "configured": False,
        "message": message,
        "fallback_url": get_charger_site_url(),
        "stations": [],
    }


def collect_station_items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("stations", "data", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = collect_station_items(value)
            if nested:
                return nested

    return []


def first_value(source: dict, keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None:
            return value
    return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_zijingang_charger_station(item: dict) -> bool:
    campus_id = first_value(item, ("campus_id", "campus"), None)
    campus_name = str(first_value(item, ("campus_name", "campus_name_cn", "area"), ""))
    return str(campus_id) == "2" or campus_name == "紫金港校区"


def normalize_charger_station(item: dict) -> dict:
    total_ports = to_int(first_value(
        item,
        ("total_ports", "total", "total_count", "pile_total", "count", "total_num"),
        0,
    ))
    available_ports = to_int(first_value(
        item,
        ("available_ports", "available", "free", "idle", "free_count", "available_num"),
        0,
    ))
    used_ports = to_int(first_value(
        item,
        ("used_ports", "used", "occupied_ports", "occupied", "busy", "used_count"),
        0,
    ))
    error_ports = to_int(first_value(
        item,
        ("error_ports", "error", "fault_ports", "fault", "broken", "offline_count"),
        0,
    ))
    campus_id = first_value(item, ("campus_id", "campus"), None)
    campus_name = first_value(item, ("campus_name", "campus_name_cn", "area"), "unknown")

    return {
        "id": str(first_value(item, ("hash_id", "id", "station_id", "name"), "unknown")),
        "name": first_value(item, ("name", "station_name", "title"), "未知站点"),
        "provider": first_value(item, ("provider", "operator", "brand"), "unknown"),
        "campus": campus_name,
        "campus_id": campus_id,
        "campus_name": campus_name,
        "status": first_value(item, ("status", "state"), "unknown"),
        "total_ports": total_ports,
        "available_ports": available_ports,
        "used_ports": used_ports,
        "occupied_ports": used_ports,
        "error_ports": error_ports,
        "fault_ports": error_ports,
        "has_available_ports": available_ports > 0,
        "updated_at": first_value(
            item,
            ("updated_at", "update_time", "last_update", "time"),
            "unknown",
        ),
        "longitude": to_float(first_value(item, ("longitude", "lng", "lon"), None)),
        "latitude": to_float(first_value(item, ("latitude", "lat"), None)),
        "devids": first_value(item, ("devids", "device_ids"), []),
    }


async def fetch_zju_charger_stations() -> dict:
    base_url = os.getenv("ZJU_CHARGER_API_BASE_URL", "").strip().rstrip("/")
    path = os.getenv("ZJU_CHARGER_STATIONS_PATH", "/api/status").strip()
    timeout_raw = os.getenv("ZJU_CHARGER_API_TIMEOUT", "8").strip()

    if not base_url:
        return charger_unavailable("未配置 ZJU_CHARGER_API_BASE_URL，当前使用 ZJU-Charger 外链兜底。")

    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 8.0

    url = f"{base_url}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return {
            "ok": False,
            "configured": True,
            "message": "ZJU-Charger API 暂不可用，当前使用外链兜底。",
            "fallback_url": get_charger_site_url(),
            "stations": [],
        }

    station_items = collect_station_items(payload)
    stations = [
        normalize_charger_station(item)
        for item in station_items
        if is_zijingang_charger_station(item)
    ]

    return {
        "ok": True,
        "configured": True,
        "message": "已通过 ZJU-Charger API 获取充电桩站点数据。",
        "fallback_url": get_charger_site_url(),
        "source_url": url,
        "stations": stations,
    }
