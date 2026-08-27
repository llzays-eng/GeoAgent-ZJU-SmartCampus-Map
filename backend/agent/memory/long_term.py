"""agent/memory/long_term.py — Cross-session persistent memory (outline
section 8, 长期记忆).

This is the layer most demo Agent projects skip entirely (they call a
Python list "memory" and stop) — the outline calls it out explicitly as
"招聘方最想看你是否理解的部分" (the part interviewers most want to see you
actually understand). Two real SQLite tables, both genuinely read from and
written to disk (verified in this repo's tests — see the __main__ block and
docs/AGENT_ARCHITECTURE.md):

  user_preferences  — "该用户总是倾向选择安静、靠近充电桩的自习室"-style
                       facts, keyed by user_id, injected into the
                       Orchestrator's system prompt at the start of a new
                       session (not every turn — see orchestrator.py).
  analysis_cache     — expensive-ish results (e.g. an NDVI run for a given
                       region+year-range) keyed by a content hash, reused
                       across *different* sessions/users, unlike
                       short_term.ToolResultCache which is per-session only.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id     TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS analysis_cache (
    cache_key   TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL
);

CREATE INDEX IF NOT EXISTS idx_user_preferences_user ON user_preferences(user_id);
"""


@dataclass
class Preference:
    key: str
    value: str
    updated_at: float


class LongTermMemoryStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- user preferences ----------------------------------------------------

    def upsert_preference(self, user_id: str, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_preferences (user_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (user_id, key, value, time.time()),
            )

    def get_preferences(self, user_id: str) -> list[Preference]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, value, updated_at FROM user_preferences WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [Preference(key=r["key"], value=r["value"], updated_at=r["updated_at"]) for r in rows]

    def preferences_prompt_snippet(self, user_id: str, limit: int = 5) -> str:
        """Rendered for injection into the Orchestrator's system prompt at
        the start of a new session (outline: "每次新会话开始时检索相关记忆
        注入system prompt")."""
        prefs = self.get_preferences(user_id)[:limit]
        if not prefs:
            return ""
        lines = [f"- {p.key}: {p.value}" for p in prefs]
        return "已知该用户的历史偏好（供参考，不要盲目套用，仍以本次query为准）：\n" + "\n".join(lines)

    # -- analysis cache -------------------------------------------------------

    @staticmethod
    def make_cache_key(kind: str, params: dict[str, Any]) -> str:
        canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
        return f"{kind}:{digest}"

    def get_cached_analysis(self, cache_key: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json, expires_at FROM analysis_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
        if row is None:
            return None
        if row["expires_at"] is not None and row["expires_at"] < time.time():
            self.delete_cached_analysis(cache_key)
            return None
        return json.loads(row["result_json"])

    def set_cached_analysis(self, cache_key: str, kind: str, result: dict[str, Any], ttl_seconds: Optional[float] = None) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds else None
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO analysis_cache (cache_key, kind, result_json, created_at, expires_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(cache_key) DO UPDATE SET result_json=excluded.result_json, created_at=excluded.created_at, expires_at=excluded.expires_at",
                (cache_key, kind, json.dumps(result, ensure_ascii=False), time.time(), expires_at),
            )

    def delete_cached_analysis(self, cache_key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM analysis_cache WHERE cache_key = ?", (cache_key,))

    def stats(self) -> dict[str, int]:
        with self._connect() as conn:
            n_prefs = conn.execute("SELECT COUNT(*) c FROM user_preferences").fetchone()["c"]
            n_cache = conn.execute("SELECT COUNT(*) c FROM analysis_cache").fetchone()["c"]
        return {"preferences": n_prefs, "analysis_cache_entries": n_cache}


_store: LongTermMemoryStore | None = None


def get_long_term_store() -> LongTermMemoryStore:
    global _store
    if _store is None:
        from agent.config import get_settings

        _store = LongTermMemoryStore(get_settings().long_term_db_path)
    return _store


if __name__ == "__main__":
    # Makes this module's own docstring claim true: a real write, a real
    # process-boundary-simulating reopen, and a real read back — not just
    # an in-memory round trip within one Python object's lifetime.
    import tempfile
    from pathlib import Path

    tmp_path = Path(tempfile.gettempdir()) / "long_term_memory_smoketest.sqlite3"
    tmp_path.unlink(missing_ok=True)

    store = LongTermMemoryStore(tmp_path)
    store.upsert_preference("smoketest-user", "quiet_room_near_charger", "偏好安静、靠近充电桩的自习室")
    cache_key = store.make_cache_key("smoketest", {"region": "紫金港校区"})
    store.set_cached_analysis(cache_key, "smoketest", {"trend": "上升"}, ttl_seconds=3600)
    del store  # drop this handle entirely before reopening, to actually exercise disk persistence

    reopened = LongTermMemoryStore(tmp_path)
    prefs = reopened.get_preferences("smoketest-user")
    cached = reopened.get_cached_analysis(cache_key)
    print(f"preferences after reopen: {prefs}")
    print(f"cached analysis after reopen: {cached}")
    print(f"stats: {reopened.stats()}")
    assert prefs and "安静" in prefs[0].value, "FAIL: preference did not survive a fresh LongTermMemoryStore instance"
    assert cached == {"trend": "上升"}, "FAIL: cached analysis did not survive a fresh LongTermMemoryStore instance"
    tmp_path.unlink(missing_ok=True)
    print("\n✓ Long-term memory genuinely persists across store instances (SQLite, not an in-memory illusion)")
