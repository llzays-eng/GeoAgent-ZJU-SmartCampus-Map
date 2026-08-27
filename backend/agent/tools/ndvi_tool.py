"""agent/tools/ndvi_tool.py — ndvi_trend(region, start_year, end_year).

Outline section 5 explicitly scopes this as a "精简版" (slimmed-down) tool
that calls GEE rather than reimplementing the full 35-year RSEI-CLCD
pipeline. Two backends:

  * "gee"       — real Google Earth Engine call (MODIS MOD13Q1 NDVI, annual
                  growing-season composite). Requires the `earthengine-api`
                  package and service-account credentials
                  (GEE_SERVICE_ACCOUNT / GEE_PRIVATE_KEY_FILE). Not
                  reachable from this build sandbox (no earthengine.googleapis.com
                  in the network allowlist), so it could not be exercised
                  while building this project — it is a real implementation
                  written for the user's own machine, not something claimed
                  to have been tested here.
  * "synthetic" — a seeded, deterministic stand-in time series used for
                  local development, demos, and everything that WAS tested
                  while building this project. It is NOT satellite data.

IMPORTANT: every value returned by this tool carries an explicit
`data_source` field ("gee" or "synthetic_demo") plus, when synthetic, a
`disclaimer` string. The Orchestrator's summarization prompt is instructed
(see orchestrator.py SUMMARIZE_SYSTEM_PROMPT) to always surface that
disclaimer to the user rather than presenting the numbers as measured data.
"""
from __future__ import annotations

import hashlib
from typing import Any

from agent.config import get_settings
from agent.memory.long_term import get_long_term_store
from agent.tools.registry import ToolSpec

# analysis_cache TTL: this is agent/memory/long_term.py's cross-session,
# cross-user cache (NOT short_term.py's per-session ToolResultCache, which
# uses AGENT_TOOL_CACHE_TTL / 600s by default — far too short for something
# meant to be reused "across sessions/users"). A generous, fixed TTL is
# enough to demonstrate the wiring actually working end-to-end; there's no
# env var for this because nothing else in the review asked for one.
_ANALYSIS_CACHE_TTL_SECONDS = 24 * 3600

SYNTHETIC_DISCLAIMER = (
    "⚠️ 当前 NDVI 数值来自本地确定性模拟生成器（synthetic_demo），并非 Google Earth Engine 真实遥感反演结果。"
    "该分支仅用于在未配置 GEE 服务账号凭证时演示 Agent 的工具调用与多子任务协作流程，不能用于真实生态分析结论。"
    "配置 GEE_SERVICE_ACCOUNT / GEE_PRIVATE_KEY_FILE 后可切换到真实的 ndvi_backend=gee。"
)


def _synthetic_ndvi_series(region: str, start_year: int, end_year: int) -> list[dict[str, Any]]:
    """Deterministic (seeded by region name) fake NDVI series — same region +
    year range always returns the same numbers, so this is reproducible and
    cache-friendly, but it is explicitly NOT real satellite data."""
    seed = int(hashlib.sha256(region.encode("utf-8")).hexdigest(), 16) % (2**32)
    # A tiny, dependency-free LCG so we don't need numpy's global RNG state
    # touched by anything else running in the same process.
    state = seed or 1
    series = []
    base = 0.42 + (seed % 100) / 1000  # per-region baseline in a plausible NDVI band
    drift = ((seed // 100) % 21 - 10) / 4000  # small per-year drift, +/-
    for i, year in enumerate(range(start_year, end_year + 1)):
        state = (1103515245 * state + 12345) % (2**31)
        noise = ((state / (2**31)) - 0.5) * 0.03
        value = max(0.05, min(0.95, base + drift * i + noise))
        series.append({"year": year, "ndvi_mean": round(value, 4)})
    return series


async def _gee_ndvi_series(region: str, start_year: int, end_year: int) -> list[dict[str, Any]] | None:
    """Real Earth Engine backend. Returns None (caller falls back to
    synthetic) on any import/auth/query failure so the tool never hard-crashes
    the agent loop just because GEE isn't reachable."""
    try:
        import ee  # earthengine-api; optional dependency, imported lazily
    except ImportError:
        return None

    settings = get_settings()
    try:
        import os as _os

        service_account = _os.getenv("GEE_SERVICE_ACCOUNT", "")
        key_file = _os.getenv("GEE_PRIVATE_KEY_FILE", "")
        if service_account and key_file:
            credentials = ee.ServiceAccountCredentials(service_account, key_file)
            ee.Initialize(credentials)
        else:
            ee.Initialize()  # relies on `earthengine authenticate` having been run
    except Exception:
        return None

    try:
        from geo_data import campus_bbox

        min_lon, min_lat, max_lon, max_lat = campus_bbox()
        aoi = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])

        series = []
        for year in range(start_year, end_year + 1):
            collection = (
                ee.ImageCollection("MODIS/061/MOD13Q1")
                .filterDate(f"{year}-06-01", f"{year}-09-30")  # growing season
                .select("NDVI")
                .mean()
            )
            stats = collection.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=aoi, scale=250, maxPixels=1e9
            ).getInfo()
            ndvi_raw = stats.get("NDVI")
            if ndvi_raw is None:
                continue
            series.append({"year": year, "ndvi_mean": round(ndvi_raw * 0.0001, 4)})  # MOD13Q1 scale factor
        return series or None
    except Exception:
        return None


async def ndvi_trend(region: str, start_year: int, end_year: int) -> dict[str, Any]:
    if end_year < start_year:
        start_year, end_year = end_year, start_year
    end_year = min(end_year, start_year + 34)  # keep the tool "精简版" — guard against absurd ranges

    settings = get_settings()

    # Cross-session/cross-user cache (agent/memory/long_term.py's
    # analysis_cache table) — previously defined but never called from
    # anywhere, so "下次同样的问题不用重跑" never actually happened. Key
    # includes ndvi_backend so switching from synthetic -> gee (or back)
    # can never serve a stale result from the other backend.
    store = get_long_term_store()
    cache_key = store.make_cache_key(
        "ndvi_trend",
        {"region": region, "start_year": start_year, "end_year": end_year, "backend": settings.ndvi_backend},
    )
    cached = store.get_cached_analysis(cache_key)
    if cached is not None:
        return cached

    if settings.ndvi_backend == "gee":
        gee_series = await _gee_ndvi_series(region, start_year, end_year)
        if gee_series:
            trend = "上升" if gee_series[-1]["ndvi_mean"] > gee_series[0]["ndvi_mean"] else "下降"
            result = {
                "ok": True,
                "region": region,
                "data_source": "gee",
                "collection": "MODIS/061/MOD13Q1",
                "series": gee_series,
                "trend": trend,
                "note": "NDVI 为生长季（6-9月）均值合成，跨年比较仅供参考——不同年份大气条件、物候期存在差异，详见知识库相关文档。",
            }
            store.set_cached_analysis(cache_key, "ndvi_trend", result, ttl_seconds=_ANALYSIS_CACHE_TTL_SECONDS)
            return result
        # GEE selected but unavailable -> fall through to synthetic, but say so loudly.

    series = _synthetic_ndvi_series(region, start_year, end_year)
    trend = "上升" if series[-1]["ndvi_mean"] > series[0]["ndvi_mean"] else "下降"
    result = {
        "ok": True,
        "region": region,
        "data_source": "synthetic_demo",
        "disclaimer": SYNTHETIC_DISCLAIMER,
        "series": series,
        "trend": trend,
        "_degraded": True,
        "_degraded_reason": SYNTHETIC_DISCLAIMER,
    }
    store.set_cached_analysis(cache_key, "ndvi_trend", result, ttl_seconds=_ANALYSIS_CACHE_TTL_SECONDS)
    return result


SPEC = ToolSpec(
    name="ndvi_trend",
    description=(
        "查询某区域指定年份范围的 NDVI（归一化植被指数）年度均值序列及趋势。"
        "默认使用本地确定性模拟数据（明确标注 data_source=synthetic_demo，非真实遥感数据），"
        "配置 GEE 服务账号后可切换为调用 Google Earth Engine 的真实 MODIS NDVI 数据。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "region": {"type": "string", "description": "区域名称，如“紫金港校区”"},
            "start_year": {"type": "integer"},
            "end_year": {"type": "integer"},
        },
        "required": ["region", "start_year", "end_year"],
    },
    handler=ndvi_trend,
    degradation_notes="No GEE credentials -> synthetic_demo series with a mandatory disclaimer field.",
)
