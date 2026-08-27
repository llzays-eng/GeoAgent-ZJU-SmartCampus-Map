"""agent/state_machine.py — Explicit workflow state machine for the Agent Loop.

Outline section 4 says to hand-write a simple state machine first (no
framework), and section 10 asks for that loop to be an *explicit*,
visualizable state graph rather than scattered try/except. This module is
that: a real transition table that is validated at import time with
networkx, an object that raises if the Orchestrator ever attempts an
illegal transition, and an event log the frontend can render as a live trace
or the README can render as a diagram.

State meaning
-------------
INTENT_PARSING  → classify the query: direct tool call / needs planning / needs RAG
RAG_RETRIEVAL   → knowledge-base lookup for "what does X mean" style questions
TASK_PLANNING   → LLM (or rule engine) emits a structured task graph
EXECUTING       → tasks dispatched to Subagents in dependency order
VALIDATING      → sanity-check each TaskResult (e.g. is the coordinate on campus?)
REPLANNING      → validation found a gap (missing data / bad result) → patch the plan
SUMMARIZING     → fold all TaskResults (+ RAG context) into the final answer
DONE / ERROR    → terminal states
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import networkx as nx

from agent.schemas import AgentEvent, AgentState

# (from_state -> {to_state, ...}) — the single source of truth for what's legal.
TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.INTENT_PARSING: {
        AgentState.RAG_RETRIEVAL,
        AgentState.TASK_PLANNING,
        AgentState.EXECUTING,  # simple, single-tool path skips planning
        AgentState.SUMMARIZING,  # pure chit-chat / no tool needed
        AgentState.ERROR,
    },
    AgentState.RAG_RETRIEVAL: {AgentState.SUMMARIZING, AgentState.ERROR},
    AgentState.TASK_PLANNING: {AgentState.EXECUTING, AgentState.ERROR},
    AgentState.EXECUTING: {AgentState.VALIDATING, AgentState.ERROR},
    AgentState.VALIDATING: {
        AgentState.SUMMARIZING,   # all good
        AgentState.REPLANNING,    # gap found, plan needs to grow
        AgentState.EXECUTING,     # bounded retry of a failed task
        AgentState.ERROR,
    },
    AgentState.REPLANNING: {AgentState.EXECUTING, AgentState.SUMMARIZING, AgentState.ERROR},
    AgentState.SUMMARIZING: {AgentState.DONE, AgentState.ERROR},
    AgentState.DONE: set(),
    AgentState.ERROR: {AgentState.SUMMARIZING},  # degrade to a fallback answer, don't just die
}


def build_graph() -> nx.DiGraph:
    g = nx.DiGraph()
    for state in AgentState:
        g.add_node(state.value)
    for src, targets in TRANSITIONS.items():
        for dst in targets:
            g.add_edge(src.value, dst.value)
    return g


# Validate once at import time: every state must be reachable from
# INTENT_PARSING, and DONE must be reachable from every non-terminal state.
# This is a real invariant check, not decoration — it fails the build if
# someone edits TRANSITIONS into something structurally broken.
def _validate() -> None:
    g = build_graph()
    reachable = nx.descendants(g, AgentState.INTENT_PARSING.value) | {AgentState.INTENT_PARSING.value}
    missing = {s.value for s in AgentState} - reachable
    if missing:
        raise RuntimeError(f"Unreachable states in agent state machine: {missing}")
    for state in AgentState:
        if state == AgentState.DONE:
            continue
        if not nx.has_path(g, state.value, AgentState.DONE.value):
            raise RuntimeError(f"State {state.value} can never reach DONE — workflow would hang")


_validate()


def to_mermaid() -> str:
    """Render the transition table as a Mermaid state diagram — used in
    docs/AGENT_ARCHITECTURE.md and can be reused by the frontend if it wants
    a static overview alongside the live trace."""
    lines = ["stateDiagram-v2", "    [*] --> intent_parsing"]
    for src, targets in TRANSITIONS.items():
        for dst in sorted(t.value for t in targets):
            lines.append(f"    {src.value} --> {dst}")
    lines.append("    done --> [*]")
    return "\n".join(lines)


@dataclass
class RunTrace:
    """Accumulates AgentEvents for one orchestrator run (one user query)."""

    events: list[AgentEvent] = field(default_factory=list)
    on_event: Callable[[AgentEvent], None] | None = None

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(event)


class StateMachine:
    """Tracks the Orchestrator's current state and enforces TRANSITIONS."""

    def __init__(self, trace: RunTrace):
        self.state = AgentState.INTENT_PARSING
        self.trace = trace
        self._history: list[AgentState] = [self.state]

    def transition(self, to: AgentState, reason: str, payload: dict | None = None) -> None:
        allowed = TRANSITIONS.get(self.state, set())
        if to not in allowed and to != self.state:
            raise ValueError(
                f"Illegal transition {self.state.value} -> {to.value}. "
                f"Allowed from {self.state.value}: {sorted(a.value for a in allowed)}"
            )
        self.state = to
        self._history.append(to)
        self.trace.emit(
            AgentEvent(state=to, kind="enter_state", message=reason, payload=payload or {})
        )

    @property
    def history(self) -> list[AgentState]:
        return list(self._history)

    @property
    def replan_count(self) -> int:
        return self._history.count(AgentState.REPLANNING)

    @property
    def retry_count(self) -> int:
        # An EXECUTING that is re-entered from VALIDATING (not from TASK_PLANNING
        # or REPLANNING) is a same-plan retry, not a fresh execution.
        retries = 0
        for prev, cur in zip(self._history, self._history[1:]):
            if cur == AgentState.EXECUTING and prev == AgentState.VALIDATING:
                retries += 1
        return retries
