"""agent/subagents/base.py — Shared Subagent execution logic (outline section 6).

Each concrete Subagent (data_retrieval / spatial_analysis / reporting) is a
thin config: a name + a permanently fixed list of allowed tool names. The
Orchestrator never calls tools directly — it only ever dispatches a
TaskSpec to the Subagent whose name matches `task.subagent`, and only that
Subagent's `execute()` touches ToolRegistry.call(). This file is the one
copy of "goal -> brain picks a tool from MY allowed set -> execute it ->
package a TaskResult" shared by all three.

One real integration detail worth calling out: `_thread_dependency_values()`
handles passing concrete data between dependent tasks (e.g. a geocode
result's coordinates feeding into the next task's search_poi call). The
brain/LLM decides *which tool* to call and with what *rough* arguments from
the goal text; this method then deterministically overwrites
location-shaped arguments with the actual upstream result if one exists.
This split — LLM decides intent, orchestration layer moves data — is what
makes multi-step chains work correctly even under the rule-fallback
backend, which has no real cross-task reasoning ability.
"""
from __future__ import annotations

from typing import Any

from agent.brain import Brain
from agent.schemas import TaskResult, TaskSpec, TaskStatus, ToolCallRecord
from agent.tools.registry import ToolRegistry


def _find_upstream_coordinate(upstream_results: list[TaskResult]) -> dict[str, float] | None:
    """Look through completed upstream TaskResults for something
    coordinate-shaped (a geocode result, or a search_poi/spatial_buffer
    result's first hit) to hand to a dependent task."""
    for result in reversed(upstream_results):  # most recent dependency wins
        data = result.data
        if "lat" in data and "lon" in data:  # geocode-shaped
            return {"lat": data["lat"], "lon": data["lon"]}
        results_list = data.get("results")
        if isinstance(results_list, list) and results_list:
            first = results_list[0]
            if "lat" in first and "lon" in first:
                return {"lat": first["lat"], "lon": first["lon"]}
    return None


def _find_upstream_chart_data(upstream_results: list[TaskResult]) -> Any | None:
    """A generate_chart task always depends_on a data-producing task (see
    llm_client._build_task_plan and skills/report_generation) — the actual
    numbers to plot should come from there, not from re-deriving them out of
    the goal text. Recognizes the two chartable shapes tools in this project
    actually produce: an NDVI `series` and a POI/study-room `results` list.
    """
    for result in reversed(upstream_results):
        data = result.data
        if isinstance(data.get("series"), list) and data["series"]:
            return data["series"]  # [{"year": ..., "ndvi_mean": ...}, ...]
        if isinstance(data.get("results"), list) and data["results"]:
            return [{"label": r.get("name", "?"), "value": r.get("distance_m", 1)} for r in data["results"]]
    return None


class BaseSubagent:
    name: str = "base"
    allowed_tools: tuple[str, ...] = ()

    def __init__(self, tool_registry: ToolRegistry, brain: Brain):
        self.tool_registry = tool_registry
        self.brain = brain

    def _tool_schemas(self) -> list[dict[str, Any]]:
        return self.tool_registry.schemas_for(list(self.allowed_tools))

    async def execute(
        self,
        task: TaskSpec,
        upstream_results: list[TaskResult],
        skill_usage_notes: str,
        conversation_context: list[dict[str, str]],
        tool_cache=None,
    ) -> TaskResult:
        goal_text = task.goal
        if upstream_results:
            upstream_summaries = "; ".join(f"{r.goal} -> {r.summary or '已完成'}" for r in upstream_results)
            goal_text = f"{task.goal}\n（前置任务参考信息：{upstream_summaries}）"

        tool_call = await self.brain.choose_tool(self.name, goal_text, self._tool_schemas(), skill_usage_notes, conversation_context)

        if tool_call is None:
            return TaskResult(
                task_id=task.id, subagent=self.name, status=TaskStatus.FAILED,  # type: ignore[arg-type]
                goal=task.goal, summary="Subagent 未选择任何工具", error="no_tool_selected",
            )

        arguments = dict(tool_call.arguments)
        if upstream_results:
            coord = _find_upstream_coordinate(upstream_results)
            if coord:
                if "center" in arguments and isinstance(arguments.get("center"), dict):
                    arguments["center"] = coord
                elif "location" in arguments and isinstance(arguments.get("location"), (dict, type(None))):
                    arguments["location"] = coord

            if tool_call.name == "generate_chart" and not arguments.get("data"):
                chart_data = _find_upstream_chart_data(upstream_results)
                if chart_data:
                    arguments["data"] = chart_data

        cached = tool_cache.get(tool_call.name, arguments) if tool_cache else None
        if cached is not None:
            record = ToolCallRecord(tool_name=tool_call.name, arguments=arguments, ok=True, result=cached, cache_hit=True)
        else:
            record = await self.tool_registry.call(tool_call.name, arguments)
            if tool_cache and record.ok:
                tool_cache.set(tool_call.name, arguments, record.result)

        if not record.ok:
            return TaskResult(
                task_id=task.id, subagent=self.name, status=TaskStatus.FAILED,  # type: ignore[arg-type]
                goal=task.goal, summary=f"工具 {tool_call.name} 调用失败", data={},
                tool_calls=[record], error=record.error,
            )

        # The Python call didn't raise, but the tool may still report its own
        # semantic failure (e.g. geocode found zero matches anywhere, charger
        # API unreachable) via a top-level {"ok": false, ...} in its result —
        # that's a real task failure even though ToolCallRecord.ok is True
        # (no exception was raised). Still keep the returned data (e.g.
        # charger's fallback_url) rather than discarding it.
        semantic_ok = record.result.get("ok", True) if isinstance(record.result, dict) else True
        if not semantic_ok:
            error_message = record.result.get("message", "工具返回 ok=false") if isinstance(record.result, dict) else "工具返回 ok=false"
            return TaskResult(
                task_id=task.id, subagent=self.name, status=TaskStatus.FAILED,  # type: ignore[arg-type]
                goal=task.goal, summary=f"{tool_call.name}: {error_message}", data=record.result or {},
                tool_calls=[record], error=error_message,
                degraded=record.degraded, degraded_reason=record.degraded_reason,
            )

        summary = self._summarize_result(tool_call.name, record.result)
        return TaskResult(
            task_id=task.id, subagent=self.name, status=TaskStatus.SUCCEEDED,  # type: ignore[arg-type]
            goal=task.goal, summary=summary, data=record.result or {}, tool_calls=[record],
            degraded=record.degraded, degraded_reason=record.degraded_reason,
        )

    @staticmethod
    def _summarize_result(tool_name: str, result: dict[str, Any] | None) -> str:
        if not result:
            return f"{tool_name} 返回空结果"
        if "count" in result:
            return f"{tool_name} 找到 {result['count']} 条结果"
        if "series" in result:
            return f"{tool_name} 返回 {len(result['series'])} 个年份的序列，趋势：{result.get('trend', '未知')}"
        if "stations" in result:
            return f"{tool_name} 返回 {len(result.get('stations', []))} 个充电站点"
        if "url_path" in result:
            return f"{tool_name} 已生成图表 {result['url_path']}"
        if "matched_address" in result:
            return f"{tool_name} 解析到坐标 ({result.get('lat')}, {result.get('lon')})"
        return f"{tool_name} 执行完成"
