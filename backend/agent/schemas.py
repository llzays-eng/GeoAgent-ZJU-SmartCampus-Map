"""agent/schemas.py — Shared data contracts for the GeoAgent system.

Keeping these in one module (rather than letting every layer invent its own
dict shape) is what lets the Orchestrator, the three Subagents, the Skill
registry, and the FastAPI layer all agree on what a "task", a "tool call", or
an "event" looks like.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SubagentName = Literal["data_retrieval", "spatial_analysis", "reporting"]


# ── Task planning (outline section 4.2 + 10) ───────────────────────────────

class TaskSpec(BaseModel):
    """One node in the Orchestrator's task graph."""

    id: str
    goal: str = Field(..., description="Natural-language goal for this subtask")
    subagent: SubagentName
    depends_on: list[str] = Field(default_factory=list)


class TaskPlan(BaseModel):
    tasks: list[TaskSpec]

    def topological_order(self) -> list[TaskSpec]:
        """Kahn's algorithm. Raises ValueError on a cycle or dangling dependency —
        the Orchestrator treats that as an invalid plan and repairs/re-plans."""
        by_id = {t.id: t for t in self.tasks}
        for t in self.tasks:
            for dep in t.depends_on:
                if dep not in by_id:
                    raise ValueError(f"task {t.id} depends on unknown task {dep}")

        remaining = {t.id: set(t.depends_on) for t in self.tasks}
        ordered: list[TaskSpec] = []
        while remaining:
            ready = [tid for tid, deps in remaining.items() if not deps]
            if not ready:
                raise ValueError("cycle detected in task plan")
            ready.sort()  # deterministic order among independently-ready tasks
            for tid in ready:
                ordered.append(by_id[tid])
                del remaining[tid]
            for deps in remaining.values():
                deps.difference_update(ready)
        return ordered


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"


class TaskResult(BaseModel):
    task_id: str
    subagent: SubagentName
    status: TaskStatus
    goal: str
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list["ToolCallRecord"] = Field(default_factory=list)
    error: Optional[str] = None
    attempt: int = 1
    degraded: bool = False
    degraded_reason: str = ""


# ── Tool calling (outline section 5) ───────────────────────────────────────

class ToolCallRecord(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    ok: bool
    result: Any = None
    error: Optional[str] = None
    elapsed_ms: float = 0.0
    degraded: bool = False  # true if the tool fell back to a non-primary data source
    degraded_reason: str = ""
    cache_hit: bool = False


# ── State machine / workflow (outline section 4 + 10) ──────────────────────

class AgentState(str, Enum):
    INTENT_PARSING = "intent_parsing"
    RAG_RETRIEVAL = "rag_retrieval"
    TASK_PLANNING = "task_planning"
    EXECUTING = "executing"
    VALIDATING = "validating"
    REPLANNING = "replanning"
    SUMMARIZING = "summarizing"
    DONE = "done"
    ERROR = "error"


class AgentEvent(BaseModel):
    """One entry in the live trace streamed to the frontend over WebSocket
    (outline section 13) and returned in full for the REST fallback."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    ts: float = Field(default_factory=time.time)
    state: AgentState
    kind: Literal["enter_state", "subagent_dispatch", "tool_call", "note", "final_answer", "error"]
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)


# ── Top-level request / response ───────────────────────────────────────────

class AgentChatRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    user_id: Optional[str] = "guest"
    # Client-generated, stable across a WS attempt and any REST fallback for
    # the SAME logical query — lets the backend dedupe a retry instead of
    # re-running the whole Agent Loop. See agent/request_dedup.py. Optional
    # and back-compatible: omitted/None simply disables dedup for that call
    # (e.g. a bare curl/test request), matching the previous behaviour.
    request_id: Optional[str] = None


class AgentChatResponse(BaseModel):
    session_id: str
    query: str
    answer: str
    mode: Literal["agent_llm", "agent_rule_fallback"] = "agent_llm"
    # `mode` reflects ONLY whether the LLM backend itself degraded to
    # rule_fallback for routing/planning/summarizing. Tool/data-source
    # degradation (no AMAP key -> local gazetteer, NDVI defaulting to
    # synthetic_demo, etc.) is a separate, independent signal — conflating
    # the two previously mislabeled fully-healthy DeepSeek runs as
    # "agent_rule_fallback" the moment they touched NDVI. See orchestrator.py.
    data_degraded: bool = False
    data_degraded_notes: list[str] = Field(default_factory=list)
    used_rag: bool = False
    rag_sources: list[dict[str, Any]] = Field(default_factory=list)
    task_plan: Optional[TaskPlan] = None
    task_results: list[TaskResult] = Field(default_factory=list)
    chart: Optional[dict[str, Any]] = None
    map_focus: list[dict[str, Any]] = Field(default_factory=list)
    events: list[AgentEvent] = Field(default_factory=list)
    elapsed_ms: float = 0.0
