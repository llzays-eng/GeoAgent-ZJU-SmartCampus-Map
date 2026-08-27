"""agent/config.py — Central configuration for the GeoAgent system.

Every setting is environment-driven (see backend/.env.example) so the same
code runs in three situations without edits:

  1. A real deployment with a DeepSeek API key and (optionally) AMap /
     Earth Engine credentials — full real function calling, real geocoding,
     real NDVI.
  2. A local dev/demo machine with *no* API key — the system degrades to the
     rule-based orchestrator + local gazetteer + synthetic NDVI, exactly the
     same "never blank-screen" philosophy the original project already used
     for its single DeepSeek call.
  3. This sandboxed build/test environment — same as (2); it's how every
     module in this package was actually verified while being built.

Nothing here should be imported for its side effects; call `get_settings()`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = AGENT_DIR.parent
ROOT_DIR = BACKEND_DIR.parent


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    # os.getenv(name, default) only substitutes `default` when the key is
    # ABSENT from the environment — a `.env` line like `KEY=` (present but
    # blank) makes it "", and int("") raises ValueError, crashing
    # get_settings() (and therefore server startup) the moment anyone edits
    # a numeric .env value down to blank instead of deleting the line
    # entirely. Read + strip first, fall back to `default` for both "absent"
    # and "present but blank" alike.
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    # -- LLM / function calling -------------------------------------------------
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    llm_backend: str  # "deepseek" | "rule_fallback"

    # -- geocoding ---------------------------------------------------------------
    amap_api_key: str
    amap_base_url: str
    geocode_backend: str  # "inprocess" | "mcp" — see agent/tools/mcp_bridge.py

    # -- NDVI / remote sensing ----------------------------------------------------
    ndvi_backend: str  # "gee" | "synthetic"

    # -- RAG -----------------------------------------------------------------------
    embedding_backend: str  # "tfidf" | "bge"
    rag_top_k: int

    # -- memory --------------------------------------------------------------------
    long_term_db_path: Path
    short_term_summary_trigger_turns: int
    short_term_keep_recent_turns: int
    tool_cache_ttl_seconds: int

    # -- misc ------------------------------------------------------------------------
    charts_output_dir: Path
    max_replans: int
    max_tool_retries: int


@lru_cache
def get_settings() -> Settings:
    has_key = bool(os.getenv("DEEPSEEK_API_KEY", "").strip())
    default_backend = "deepseek" if (has_key and _truthy("AI_RECOMMENDER_ENABLED", "true")) else "rule_fallback"

    charts_dir = BACKEND_DIR / "agent_outputs" / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    # NOTE: os.getenv(key, default) only falls back to `default` when the key
    # is ABSENT from the environment — a `.env` line like
    # `AGENT_LONG_TERM_DB_PATH=` (present, but blank, exactly what this
    # project's own .env.example/README instruct for "use the default")
    # sets it to "", which os.getenv happily returns as-is. Path("") then
    # resolves to the current directory, and sqlite3.connect() on that
    # fails with "unable to open database file" — not a typo away from a
    # working path, a real crash on server startup. Read + strip first,
    # THEN decide whether to fall back, so "absent" and "present-but-blank"
    # behave identically.
    long_term_db_raw = os.getenv("AGENT_LONG_TERM_DB_PATH", "").strip()
    long_term_db = Path(long_term_db_raw) if long_term_db_raw else (BACKEND_DIR / "agent_outputs" / "long_term_memory.sqlite3")
    long_term_db.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        llm_backend=os.getenv("AGENT_LLM_BACKEND", default_backend).strip().lower(),
        amap_api_key=os.getenv("AMAP_API_KEY", "").strip(),
        amap_base_url=os.getenv("AMAP_BASE_URL", "https://restapi.amap.com/v3/geocode/geo").strip(),
        geocode_backend=os.getenv("GEOCODE_BACKEND", "inprocess").strip().lower(),
        ndvi_backend=os.getenv("NDVI_BACKEND", "synthetic").strip().lower(),
        embedding_backend=os.getenv("EMBEDDING_BACKEND", "tfidf").strip().lower(),
        rag_top_k=_int_env("RAG_TOP_K", 3),
        long_term_db_path=long_term_db,
        short_term_summary_trigger_turns=_int_env("AGENT_SUMMARY_TRIGGER_TURNS", 10),
        short_term_keep_recent_turns=_int_env("AGENT_KEEP_RECENT_TURNS", 4),
        tool_cache_ttl_seconds=_int_env("AGENT_TOOL_CACHE_TTL", 600),
        charts_output_dir=charts_dir,
        max_replans=_int_env("AGENT_MAX_REPLANS", 2),
        max_tool_retries=_int_env("AGENT_MAX_TOOL_RETRIES", 2),
    )
