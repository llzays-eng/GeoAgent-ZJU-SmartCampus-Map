"""agent/request_dedup.py — idempotency guard for /api/agent/chat and
/api/agent/ws.

The bug this fixes: agentApi.js's WebSocket client falls back to a REST
retry of the *same logical request* whenever the socket closes before a
'final' message arrives (see runAgentQuery's onclose/onerror handlers).
That covers plenty of pure transport hiccups — proxy drops the connection,
tab briefly loses network — where the backend's orchestrator.run() is still
executing server-side, or has already finished, and simply never got the
chance to push its result back over the now-dead socket. Without this
guard, the REST retry re-runs the entire Agent Loop a second time: a second
real LLM call (billed twice), and the user's turn recorded twice in
conversation history.

The frontend now tags every logical query with a client-generated
`request_id` that survives the WS -> REST fallback unchanged (same id on
both attempts — see agentApi.js). This module keys a process-local table on
that id so concurrent/duplicate callers share ONE orchestrator.run()
execution instead of starting a new one:

  - id never seen before -> caller runs the coroutine, result cached under the id
  - id currently running  -> caller awaits the SAME in-flight task
  - id already finished   -> caller gets the cached result immediately

In-process only, matching every other piece of request-scoped state in this
project (agent/memory/short_term.py's ToolResultCache carries the identical
"single-worker FastAPI dev/course-project deployment" caveat) — a
multi-worker deployment would need this in something shared like Redis
instead.

Note on the WS side specifically: if a request_id happens to already be
in-flight from a DIFFERENT connection (started by an earlier WS attempt, or
by a REST call), a new WS connection joining that same in-flight task will
correctly receive the eventual final result, but will NOT see that other
execution's intermediate AgentEvents streamed to it (those were already
sent to the original caller's on_event callback). It waits quietly and then
gets the final answer — a degraded-but-still-correct experience, and a
strict improvement over the previous behaviour of just running everything
twice. The realistic case this module targets (WS drops, REST retries) is
unaffected by this caveat, since REST never streamed events anyway.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

_ENTRY_TTL_SECONDS = 300.0  # generous margin over any plausible WS-drop -> REST-retry gap


@dataclass
class _Entry:
    task: "asyncio.Task[Any]"
    created_at: float


class PendingRequestRegistry:
    """Maps a client-supplied request_id to the in-flight/finished
    asyncio.Task running that request, so a retried request_id reuses the
    same execution instead of triggering a second one."""

    def __init__(self, ttl_seconds: float = _ENTRY_TTL_SECONDS) -> None:
        self._entries: dict[str, _Entry] = {}
        self.ttl_seconds = ttl_seconds

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [rid for rid, entry in self._entries.items() if now - entry.created_at > self.ttl_seconds]
        for rid in expired:
            del self._entries[rid]

    async def run_deduped(self, request_id: str | None, coro_factory: Callable[[], Awaitable[Any]]) -> Any:
        """Run `coro_factory()` under `request_id`'s dedup key, or attach to
        an already-running/finished execution for the same id.

        No request_id (None/empty) -> dedup is impossible and skipped
        entirely; just run it. Keeps older/simpler clients (a bare curl
        call, an automated test) working exactly as before this change.
        """
        if not request_id:
            return await coro_factory()

        self._evict_expired()
        entry = self._entries.get(request_id)
        if entry is None:
            task: "asyncio.Task[Any]" = asyncio.ensure_future(coro_factory())
            entry = _Entry(task=task, created_at=time.time())
            self._entries[request_id] = entry
        return await entry.task


_registry: PendingRequestRegistry | None = None


def get_request_dedup_registry() -> PendingRequestRegistry:
    global _registry
    if _registry is None:
        _registry = PendingRequestRegistry()
    return _registry
