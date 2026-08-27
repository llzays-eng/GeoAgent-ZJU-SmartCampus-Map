import json
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from charger_data import (
    fetch_zju_charger_stations,
    get_charger_site_url,
)
from geo_data import (
    DATA_DIR,
    EMPTY_FEATURE_COLLECTION,
    ROOT_DIR,
    haversine_m as _haversine_m,
    point_coords as _point_coords,
    read_geojson,
    safe_properties,
)

load_dotenv()

app = FastAPI(title="Zijingang WebGIS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── GeoAgent (upgrade) ──────────────────────────────────────────────────────
# Everything above this block is the original project, unmodified in
# behaviour (only refactored to import shared geo_data/charger_data helpers
# instead of duplicating them — see those modules' docstrings). The agent
# system lives entirely under backend/agent/ and is mounted here as an
# additive router + static mount; nothing below depends on it and nothing
# above is required by it.
from agent.config import get_settings as _agent_get_settings  # noqa: E402
from agent_routes import router as agent_router  # noqa: E402

app.include_router(agent_router)
app.mount("/agent-outputs/charts", StaticFiles(directory=str(_agent_get_settings().charts_output_dir)), name="agent-charts")


class StudyRoomRecommendationRequest(BaseModel):
    query: str


def truthy_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def summarize_study_room(feature: dict) -> dict:
    properties = safe_properties(feature)
    return {
        "id": properties.get("id"),
        "name": properties.get("name"),
        "building": properties.get("building"),
        "floor": properties.get("floor"),
        "room": properties.get("room"),
        "type": properties.get("type"),
        "open_time": properties.get("open_time"),
        "close_time": properties.get("close_time"),
        "seat_available": properties.get("seat_available"),
        "has_power": properties.get("has_power"),
        "noise_level": properties.get("noise_level"),
        "power_outlet_level": properties.get("power_outlet_level"),
        "group_study": properties.get("group_study"),
        "overnight_available": properties.get("overnight_available"),
        "nearby_facilities": properties.get("nearby_facilities"),
        "tags": properties.get("tags"),
        "description": properties.get("description"),
    }


def normalize_recommendation(item: dict[str, Any]) -> dict:
    return {
        "study_room_id": item.get("study_room_id") or item.get("id"),
        "name": item.get("name") or "未知自习室",
        "reason": item.get("reason") or "AI 未返回明确理由。",
        "matched_needs": item.get("matched_needs") or [],
        "notes": item.get("notes") or "推荐仅基于当前数据，实际可用情况请以现场为准。",
    }


def keyword_score(query: str, room: dict) -> tuple[int, list[str]]:
    text = query.lower()
    score = 0
    matched_needs: list[str] = []

    seat_available = room.get("seat_available")
    if isinstance(seat_available, (int, float)) and seat_available > 0:
        score += 3
        matched_needs.append("有可用座位")

    if any(word in text for word in ["安静", "quiet", "复习", "专注"]):
        if room.get("noise_level") == "quiet" or room.get("type") == "quiet":
            score += 4
            matched_needs.append("安静")

    if any(word in text for word in ["讨论", "小组", "group", "合作"]):
        if room.get("group_study") is True or room.get("type") == "discussion":
            score += 4
            matched_needs.append("适合小组讨论")

    if any(word in text for word in ["通宵", "晚上", "夜间", "熬夜", "overnight"]):
        if room.get("overnight_available") is True or room.get("type") == "overnight":
            score += 4
            matched_needs.append("夜间可用")

    if any(word in text for word in ["插座", "电源", "充电", "power"]):
        if room.get("has_power") is True or room.get("power_outlet_level") == "many":
            score += 4
            matched_needs.append("电源条件较好")

    return score, matched_needs


def fallback_recommend(query: str, rooms: list[dict]) -> list[dict]:
    scored = []
    for room in rooms:
        score, matched_needs = keyword_score(query, room)
        scored.append((score, room, matched_needs))

    scored.sort(
        key=lambda item: (
            item[0],
            item[1].get("seat_available")
            if isinstance(item[1].get("seat_available"), (int, float))
            else 0,
        ),
        reverse=True,
    )

    recommendations = []
    for score, room, matched_needs in scored[:3]:
        recommendations.append(
            {
                "study_room_id": room.get("id"),
                "name": room.get("name") or "未知自习室",
                "reason": "根据当前自习室数据和关键词规则推荐。"
                if score > 0
                else "当前需求没有明显匹配项，按可用座位等基础信息推荐。",
                "matched_needs": matched_needs,
                "notes": "当前为本地规则兜底推荐；配置 AI API Key 后可使用大模型推荐。",
            }
        )

    return recommendations


def build_ai_messages(query: str, rooms: list[dict]) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "你是浙江大学紫金港校区自习室推荐助手。"
                "你只能基于提供的自习室候选数据推荐，不要编造不存在的自习室。"
                "不要提供路线规划或校园导航建议。"
                "请严格返回 JSON，对象包含 recommendations 数组。"
                "每个推荐项包含 study_room_id、name、reason、matched_needs、notes。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "user_query": query,
                    "candidate_study_rooms": rooms,
                    "output_language": "zh-CN",
                    "max_recommendations": 3,
                },
                ensure_ascii=False,
            ),
        },
    ]


async def call_deepseek(query: str, rooms: list[dict]) -> list[dict]:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    payload = {
        "model": model,
        "messages": build_ai_messages(query, rooms),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    recommendations = parsed.get("recommendations")
    if not isinstance(recommendations, list):
        raise ValueError("AI response does not contain recommendations")

    return [normalize_recommendation(item) for item in recommendations[:3]]


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "project": "zijingang-webgis"}


@app.get("/api/study-rooms")
def study_rooms() -> dict:
    return read_geojson("study_rooms.geojson")


@app.get("/api/pois")
def pois() -> dict:
    return read_geojson("campus_pois.geojson")


@app.get("/api/buildings")
def buildings() -> dict:
    return read_geojson("buildings.geojson")


@app.get("/api/zjg-boundary")
def zjg_boundary() -> dict:
    return read_geojson("zjg.geojson")


@app.get("/api/config")
def config() -> dict:
    return {
        "charger_url": get_charger_site_url(),
        "charger_embed_mode": False,
        "ai_recommender_enabled": truthy_env("AI_RECOMMENDER_ENABLED")
        and bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
    }


@app.get("/api/chargers/status")
async def charger_status() -> dict:
    base_url = os.getenv("ZJU_CHARGER_API_BASE_URL", "").strip()
    return {
        "ok": True,
        "api_configured": bool(base_url),
        "api_base_url": base_url or None,
        "stations_path": os.getenv("ZJU_CHARGER_STATIONS_PATH", "/api/status"),
        "fallback_url": get_charger_site_url(),
        "message": "ZJU-Charger API 已配置。"
        if base_url
        else "ZJU-Charger API 尚未配置，前端应显示外链兜底。",
    }


@app.get("/api/chargers/stations")
async def charger_stations() -> dict:
    return await fetch_zju_charger_stations()


def geoserver_base_url() -> str:
    return os.getenv("GEOSERVER_URL", "http://127.0.0.1:8080/geoserver").rstrip("/")


def geoserver_workspace() -> str:
    return os.getenv("GEOSERVER_WORKSPACE", "webgis")


@app.get("/api/geoserver/status")
async def geoserver_status() -> dict:
    """检测 GeoServer 是否可达（使用公开 WFS 端点，无需认证）。"""
    base = geoserver_base_url()
    workspace = geoserver_workspace()
    # 用 WFS GetCapabilities 探测，不需要登录
    url = f"{base}/{workspace}/ows?service=WFS&version=1.0.0&request=GetCapabilities"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url)
            response.raise_for_status()
        return {
            "ok": True,
            "reachable": True,
            "message": "GeoServer 连接正常。",
            "base_url": base,
        }
    except Exception:
        return {
            "ok": False,
            "reachable": False,
            "message": "GeoServer 暂不可用，请确认服务已启动。",
            "base_url": base,
        }


@app.get("/api/geoserver/wfs")
async def geoserver_wfs(layer: str = "campus_pois") -> dict:
    """代理 GeoServer WFS GetFeature 请求，返回 GeoJSON。"""
    base = geoserver_base_url()
    workspace = geoserver_workspace()
    url = (
        f"{base}/{workspace}/ows"
        f"?service=WFS&version=1.0.0&request=GetFeature"
        f"&typeName={workspace}:{layer}"
        f"&outputFormat=application/json"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
        features = data.get("features")
        if not isinstance(features, list):
            return {"type": "FeatureCollection", "features": []}
        return {"type": "FeatureCollection", "features": features}
    except Exception:
        return {"type": "FeatureCollection", "features": []}


@app.post("/api/ai/recommend-study-room")
async def recommend_study_room(request: StudyRoomRecommendationRequest) -> dict:
    query = request.query.strip()
    study_room_data = read_geojson("study_rooms.geojson")
    rooms = [summarize_study_room(feature) for feature in study_room_data["features"]]

    if not rooms:
        return {
            "ok": False,
            "mode": "empty",
            "message": "暂无自习室数据，无法推荐。",
            "query": query,
            "recommendations": [],
        }

    if not query:
        return {
            "ok": False,
            "mode": "invalid",
            "message": "请输入你的学习需求。",
            "query": query,
            "recommendations": [],
        }

    ai_enabled = truthy_env("AI_RECOMMENDER_ENABLED")
    has_api_key = bool(os.getenv("DEEPSEEK_API_KEY", "").strip())

    if ai_enabled and has_api_key:
        try:
            recommendations = await call_deepseek(query, rooms)
            return {
                "ok": True,
                "mode": "ai",
                "message": "已根据你的需求生成 AI 推荐。",
                "query": query,
                "recommendations": recommendations,
            }
        except Exception:
            recommendations = fallback_recommend(query, rooms)
            return {
                "ok": True,
                "mode": "fallback",
                "message": "AI 服务暂不可用，当前使用本地规则兜底推荐。",
                "query": query,
                "recommendations": recommendations,
            }

    recommendations = fallback_recommend(query, rooms)
    return {
        "ok": True,
        "mode": "fallback",
        "message": "未配置 AI API Key，当前使用本地规则兜底推荐。",
        "query": query,
        "recommendations": recommendations,
    }


@app.get("/api/spatial/nearest-pois")
def spatial_nearest_pois(
    lat: float = Query(..., description="Query latitude (WGS-84)"),
    lng: float = Query(..., description="Query longitude (WGS-84)"),
    radius_m: float = Query(0, description="If > 0, only return POIs within this radius (metres)"),
    category: str = Query("", description="Optional POI category filter"),
    limit: int = Query(10, ge=1, le=50, description="Maximum results to return"),
) -> dict:
    """
    Spatial nearest-POI query.

    * ``radius_m = 0``  → return the nearest *limit* POIs regardless of distance.
    * ``radius_m > 0``  → return all POIs within *radius_m* metres, up to *limit*.

    Results are sorted by distance ascending.
    """
    poi_data = read_geojson("campus_pois.geojson")
    results: list[dict] = []

    for feature in poi_data["features"]:
        pt = _point_coords(feature)
        if pt is None:
            continue
        feat_lng, feat_lat = pt
        dist = _haversine_m(lat, lng, feat_lat, feat_lng)

        props = safe_properties(feature)
        if category and props.get("category", "") != category:
            continue
        if radius_m > 0 and dist > radius_m:
            continue

        results.append({"feature": feature, "distance_m": round(dist, 1)})

    results.sort(key=lambda x: x["distance_m"])
    clipped = results[:limit]

    return {
        "query": {
            "lat": lat,
            "lng": lng,
            "radius_m": radius_m if radius_m > 0 else None,
            "category": category or None,
            "limit": limit,
        },
        "count": len(clipped),
        "results": clipped,
    }


@app.get("/api/spatial/nearest-study-rooms")
def spatial_nearest_study_rooms(
    lat: float = Query(..., description="Query latitude (WGS-84)"),
    lng: float = Query(..., description="Query longitude (WGS-84)"),
    radius_m: float = Query(0, description="If > 0, restrict to this radius (metres)"),
    limit: int = Query(5, ge=1, le=20, description="Maximum results"),
) -> dict:
    """Return study rooms sorted by distance from (lat, lng)."""
    sr_data = read_geojson("study_rooms.geojson")
    results: list[dict] = []

    for feature in sr_data["features"]:
        pt = _point_coords(feature)
        if pt is None:
            continue
        feat_lng, feat_lat = pt
        dist = _haversine_m(lat, lng, feat_lat, feat_lng)
        if radius_m > 0 and dist > radius_m:
            continue
        results.append({"feature": feature, "distance_m": round(dist, 1)})

    results.sort(key=lambda x: x["distance_m"])
    clipped = results[:limit]

    return {
        "query": {"lat": lat, "lng": lng, "radius_m": radius_m if radius_m > 0 else None, "limit": limit},
        "count": len(clipped),
        "results": clipped,
    }
