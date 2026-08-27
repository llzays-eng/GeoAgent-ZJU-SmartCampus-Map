"""agent_routes.py — FastAPI surface for the GeoAgent Orchestrator.

Mounted from main.py alongside the project's original endpoints (which are
untouched). Three endpoints:

  POST /api/agent/chat   — non-streaming: runs the full Agent Loop, returns
                            the final AgentChatResponse (answer + task trace
                            + events all at once). Simple to call from
                            anything, including curl/Postman.
  WS   /api/agent/ws     — streaming: same Agent Loop, but AgentEvents are
                            pushed to the client the moment they happen (see
                            outline section 13 — this is what
                            AgentPanel.vue's live trace view connects to).
  GET  /api/agent/info   — introspection: which tools/skills are registered,
                            which LLM backend is active, session/memory
                            stats. Useful for the frontend's status display
                            and for sanity-checking a deployment.
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agent.config import get_settings
from agent.memory.long_term import get_long_term_store
from agent.memory.short_term import get_session_memory_store
from agent.orchestrator import get_orchestrator
from agent.request_dedup import get_request_dedup_registry
from agent.schemas import AgentChatRequest, AgentChatResponse
from agent.skills.registry import get_skill_registry
from agent.tools.registry import get_tool_registry

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(request: AgentChatRequest) -> AgentChatResponse:
    orchestrator = get_orchestrator()
    dedup = get_request_dedup_registry()
    # See agent/request_dedup.py: if request.request_id matches an
    # in-flight/just-finished WS run of the same logical query (the
    # WS-dropped-so-the-frontend-retried-over-REST case), this reuses that
    # execution's result instead of re-running the whole Agent Loop.
    return await dedup.run_deduped(
        request.request_id,
        lambda: orchestrator.run(request.query, request.session_id, request.user_id or "guest"),
    )


@router.websocket("/ws")
async def agent_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    orchestrator = get_orchestrator()
    dedup = get_request_dedup_registry()
    try:
        while True:
            payload = await websocket.receive_json()
            query = payload.get("query", "")
            session_id = payload.get("session_id")
            user_id = payload.get("user_id", "guest")
            request_id = payload.get("request_id")
            if not query:
                await websocket.send_json({"type": "error", "message": "query 不能为空"})
                continue

            async def on_event(event, ws=websocket):
                await ws.send_json({"type": "event", "event": event.model_dump(mode="json")})

            # StateMachine.transition() calls on_event synchronously, but our
            # handler needs to be async (to await websocket.send_json) — wrap
            # with a small sync-callback shim that schedules the coroutine.
            import asyncio

            loop = asyncio.get_event_loop()
            pending: list[asyncio.Task] = []

            def sync_on_event(event, loop=loop, pending=pending, ws=websocket):
                pending.append(loop.create_task(on_event(event, ws)))

            response = await dedup.run_deduped(
                request_id,
                lambda: orchestrator.run(query, session_id, user_id, on_event=sync_on_event),
            )
            if pending:
                await asyncio.gather(*pending)
            await websocket.send_json({"type": "final", "response": response.model_dump(mode="json")})
    except WebSocketDisconnect:
        pass


@router.get("/info")
async def agent_info() -> dict:
    settings = get_settings()
    tool_registry = get_tool_registry()
    skill_registry = get_skill_registry()
    memory_store = get_session_memory_store()
    long_term = get_long_term_store()

    return {
        "llm_backend": settings.llm_backend,
        "llm_has_api_key": bool(settings.deepseek_api_key),
        "ndvi_backend": settings.ndvi_backend,
        "embedding_backend": settings.embedding_backend,
        "tools": tool_registry.all_names(),
        "skills": [{"name": s.name, "description": s.description, "tools": s.tools} for s in skill_registry.list_summaries()],
        "active_sessions": memory_store.active_session_count(),
        "long_term_memory": long_term.stats(),
    }
