"""agent/tools/registry.py — Tool registry for real LLM function calling.

Outline section 5 is explicit: tools must be invoked through the LLM API's
*real* function-calling mechanism (the `tools` / `tool_calls` fields DeepSeek,
Qwen, GLM and Kimi all expose in an OpenAI-compatible way), not "ask the
model to emit JSON and regex it out" — which is what the original project's
single `call_deepseek()` did. Every ToolSpec below declares a JSON Schema
that is handed to the LLM verbatim; the LLM's `tool_calls` response is what
selects the function and arguments, and `ToolRegistry.call()` is the only
place that actually executes anything.
"""
from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agent.schemas import ToolCallRecord

ToolHandler = Callable[..., Awaitable[dict[str, Any]]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (draft-07 subset OpenAI/DeepSeek accept)
    handler: ToolHandler
    # Which failure modes are expected & handled *inside* the handler itself
    # (e.g. "no AMAP key -> local gazetteer"). Purely documentation, surfaced
    # in the Skill README so it's clear this isn't optional per outline 5.
    degradation_notes: str = ""

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def all_names(self) -> list[str]:
        return list(self._tools.keys())

    def schemas_for(self, names: list[str]) -> list[dict[str, Any]]:
        return [self.get(n).to_openai_tool() for n in names]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolCallRecord:
        start = time.perf_counter()
        try:
            spec = self.get(name)
        except KeyError as exc:
            return ToolCallRecord(
                tool_name=name, arguments=arguments, ok=False, error=str(exc),
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        try:
            result = spec.handler(**arguments)
            if inspect.isawaitable(result):
                result = await result
        except TypeError as exc:
            # Bad / missing arguments from the LLM — a very common real
            # function-calling failure mode. Surface it as a failed
            # ToolCallRecord so the Orchestrator's VALIDATING state can
            # retry with a corrective message instead of crashing.
            return ToolCallRecord(
                tool_name=name, arguments=arguments, ok=False,
                error=f"invalid arguments: {exc}",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )
        except Exception as exc:  # noqa: BLE001 - tool failures must degrade, never 500
            return ToolCallRecord(
                tool_name=name, arguments=arguments, ok=False, error=str(exc),
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        elapsed_ms = (time.perf_counter() - start) * 1000
        degraded = bool(result.get("_degraded")) if isinstance(result, dict) else False
        degraded_reason = result.pop("_degraded_reason", "") if isinstance(result, dict) else ""
        if isinstance(result, dict):
            result.pop("_degraded", None)

        return ToolCallRecord(
            tool_name=name,
            arguments=arguments,
            ok=True,
            result=result,
            elapsed_ms=elapsed_ms,
            degraded=degraded,
            degraded_reason=degraded_reason,
        )


_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """Singleton so every subagent shares one set of registered tools."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _populate_registry(_registry)
    return _registry


def _populate_registry(registry: ToolRegistry) -> None:
    # Imported lazily to avoid a circular import (tool modules import
    # ToolSpec from this file).
    from agent.tools.chart_tool import SPEC as chart_spec
    from agent.tools.charger_tool import SPEC as charger_spec
    from agent.tools.geocode_tool import SPEC as geocode_spec
    from agent.tools.ndvi_tool import SPEC as ndvi_spec
    from agent.tools.poi_tools import SEARCH_POI_SPEC, SPATIAL_BUFFER_SPEC

    for spec in (SEARCH_POI_SPEC, SPATIAL_BUFFER_SPEC, geocode_spec, charger_spec, ndvi_spec, chart_spec):
        registry.register(spec)
