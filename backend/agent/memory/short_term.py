"""agent/memory/short_term.py — Session-scoped memory (outline section 8, 短期记忆).

Two responsibilities, kept in one module because they share a session
lifecycle:

  1. ConversationMemory — keeps recent turns verbatim, and once a session
     passes `summary_trigger_turns` (default 10), folds the older turns into
     a running natural-language summary instead of letting the context grow
     without bound. This is a direct extension of the "长对话摘要压缩" the
     outline calls out as something the user already has experience with
     from prior projects.

  2. ToolResultCache — memoizes (tool_name, arguments) -> result for the
     lifetime of one session with a TTL, so asking about the same address
     twice in one conversation doesn't re-hit geocode/AMap. This is
     explicitly session-scoped (NOT the cross-session cache — that's
     long_term.py's analysis_cache, which persists results like NDVI runs
     across different sessions/users).

Storage: in-process dict, keyed by session_id. This is intentionally simple
for a single-worker FastAPI dev/course-project deployment; a production
multi-worker deployment would swap this for Redis without changing the
class interface (documented in docs/AGENT_ARCHITECTURE.md).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

Summarizer = Callable[[list[dict[str, str]]], Awaitable[str]]


@dataclass
class ConversationMemory:
    session_id: str
    summary_trigger_turns: int = 10
    keep_recent_turns: int = 4
    messages: list[dict[str, str]] = field(default_factory=list)  # [{role, content}]
    running_summary: str = ""

    def add_turn(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    @property
    def turn_count(self) -> int:
        return len(self.messages)

    def needs_summarization(self) -> bool:
        return self.turn_count > self.summary_trigger_turns

    async def maybe_summarize(self, summarizer: Optional[Summarizer] = None) -> bool:
        """If over the trigger, fold everything except the most recent
        `keep_recent_turns` messages into running_summary. Returns True if
        summarization happened. `summarizer` is an async callable (usually
        the LLM client); if None, or if it raises, falls back to a
        deterministic rule-based condenser so this never breaks the
        conversation loop.
        """
        if not self.needs_summarization():
            return False

        to_fold = self.messages[: -self.keep_recent_turns]
        remaining = self.messages[-self.keep_recent_turns :]

        new_summary_piece: str
        if summarizer is not None:
            try:
                new_summary_piece = await summarizer(to_fold)
            except Exception:
                new_summary_piece = self._rule_based_condense(to_fold)
        else:
            new_summary_piece = self._rule_based_condense(to_fold)

        self.running_summary = (
            f"{self.running_summary}\n{new_summary_piece}".strip()
            if self.running_summary
            else new_summary_piece
        )
        self.messages = remaining
        return True

    @staticmethod
    def _rule_based_condense(turns: list[dict[str, str]]) -> str:
        """Fallback summarizer: not an LLM call, just a bounded bullet list
        of user asks + assistant conclusions. Deterministic and free —
        exactly the "degrade to rule engine" pattern used elsewhere in this
        project (fallback_recommend, rule_fallback LLM backend, etc).
        """
        bullets = []
        for turn in turns:
            role = "用户" if turn["role"] == "user" else "助手"
            text = turn["content"].strip().replace("\n", " ")
            if len(text) > 60:
                text = text[:60] + "…"
            bullets.append(f"- {role}: {text}")
        return "（历史对话摘要）\n" + "\n".join(bullets)

    def context_messages(self) -> list[dict[str, str]]:
        """What to actually feed the LLM: an optional summary system note
        followed by the verbatim recent turns."""
        out: list[dict[str, str]] = []
        if self.running_summary:
            out.append({"role": "system", "content": f"以下是更早对话的摘要，供参考：\n{self.running_summary}"})
        out.extend(self.messages)
        return out


@dataclass
class _CacheEntry:
    result: Any
    expires_at: float


class ToolResultCache:
    def __init__(self, ttl_seconds: int = 600):
        self.ttl_seconds = ttl_seconds
        self._store: dict[str, _CacheEntry] = {}

    @staticmethod
    def _key(tool_name: str, arguments: dict[str, Any]) -> str:
        canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return f"{tool_name}:{digest}"

    def get(self, tool_name: str, arguments: dict[str, Any]) -> Any | None:
        key = self._key(tool_name, arguments)
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.time():
            del self._store[key]
            return None
        return entry.result

    def set(self, tool_name: str, arguments: dict[str, Any], result: Any) -> None:
        key = self._key(tool_name, arguments)
        self._store[key] = _CacheEntry(result=result, expires_at=time.time() + self.ttl_seconds)

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._store)}


class SessionMemoryStore:
    """Owns ConversationMemory + ToolResultCache per session_id."""

    def __init__(self, summary_trigger_turns: int = 10, keep_recent_turns: int = 4, tool_cache_ttl: int = 600):
        self._conversations: dict[str, ConversationMemory] = {}
        self._tool_caches: dict[str, ToolResultCache] = {}
        self.summary_trigger_turns = summary_trigger_turns
        self.keep_recent_turns = keep_recent_turns
        self.tool_cache_ttl = tool_cache_ttl

    def conversation(self, session_id: str) -> ConversationMemory:
        if session_id not in self._conversations:
            self._conversations[session_id] = ConversationMemory(
                session_id=session_id,
                summary_trigger_turns=self.summary_trigger_turns,
                keep_recent_turns=self.keep_recent_turns,
            )
        return self._conversations[session_id]

    def tool_cache(self, session_id: str) -> ToolResultCache:
        if session_id not in self._tool_caches:
            self._tool_caches[session_id] = ToolResultCache(ttl_seconds=self.tool_cache_ttl)
        return self._tool_caches[session_id]

    def active_session_count(self) -> int:
        return len(self._conversations)


_store: SessionMemoryStore | None = None


def get_session_memory_store() -> SessionMemoryStore:
    global _store
    if _store is None:
        from agent.config import get_settings

        settings = get_settings()
        _store = SessionMemoryStore(
            summary_trigger_turns=settings.short_term_summary_trigger_turns,
            keep_recent_turns=settings.short_term_keep_recent_turns,
            tool_cache_ttl=settings.tool_cache_ttl_seconds,
        )
    return _store
