"""agent/orchestrator.py — The Agent Loop (outline sections 4 + 10).

This is the one place that drives agent/state_machine.py's StateMachine
through a real run. Three branches out of INTENT_PARSING:

  direct_tool -> EXECUTING (single tool call) -> VALIDATING -> SUMMARIZING
  task_plan   -> TASK_PLANNING -> EXECUTING (topological dispatch to
                Subagents) -> VALIDATING (may loop back to EXECUTING for a
                bounded retry, or to REPLANNING to patch the plan) -> SUMMARIZING
  rag         -> RAG_RETRIEVAL -> SUMMARIZING
  chitchat    -> straight to SUMMARIZING with the model's own content

Two concrete, testable validation/replanning behaviours (not just a
try/except wrapper — see docs/AGENT_ARCHITECTURE.md for why these two were
chosen as the representative cases):

  1. Empty-result retry: a search_poi/spatial_buffer task that came back
     with zero hits gets retried once with a larger radius before being
     accepted as "genuinely nothing nearby".
  2. Missing-dependency replanning: if a task fails and something else in
     the plan depends on it, the Orchestrator inserts a new data_retrieval
     task (falling back to the campus reference point) and re-enters
     EXECUTING — this is the literal "发现数据缺失，需要新增一个数据检索
     子任务" example from the project outline, not a hypothetical.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from agent.brain import get_brain
from agent.config import get_settings
from agent.memory.long_term import get_long_term_store
from agent.memory.short_term import get_session_memory_store
from agent.rag.retriever import retrieve
from agent.schemas import (
    AgentChatResponse,
    AgentEvent,
    AgentState,
    TaskPlan,
    TaskResult,
    TaskSpec,
    TaskStatus,
    ToolCallRecord,
)
from agent.skills.registry import get_skill_registry
from agent.state_machine import RunTrace, StateMachine
from agent.subagents.registry import SUBAGENT_CLASSES
from agent.tools.registry import get_tool_registry

_EMPTY_RETRYABLE_TOOLS = {"search_poi", "spatial_buffer"}


class Orchestrator:
    def __init__(self) -> None:
        self.brain = get_brain()
        self.tool_registry = get_tool_registry()
        self.skill_registry = get_skill_registry()
        self.memory_store = get_session_memory_store()
        self.long_term = get_long_term_store()
        self.settings = get_settings()

    async def run(self, query: str, session_id: Optional[str], user_id: str, on_event=None) -> AgentChatResponse:
        start = time.perf_counter()
        session_id = session_id or uuid.uuid4().hex[:12]
        trace = RunTrace(on_event=on_event)
        sm = StateMachine(trace)

        conv = self.memory_store.conversation(session_id)
        tool_cache = self.memory_store.tool_cache(session_id)
        is_new_session = conv.turn_count == 0

        conversation_context = conv.context_messages()
        if is_new_session:
            pref_snippet = self.long_term.preferences_prompt_snippet(user_id)
            if pref_snippet:
                conversation_context = [{"role": "system", "content": pref_snippet}, *conversation_context]

        conv.add_turn("user", query)

        skill_menu = self.skill_registry.prompt_menu()
        direct_tool_schemas = self.tool_registry.schemas_for(self.tool_registry.all_names())

        intent = await self.brain.parse_intent(query, skill_menu, direct_tool_schemas, conversation_context)
        # Two independent degradation buckets, not one merged flag: `mode`
        # (the response's headline "was this really LLM-driven?" signal)
        # must reflect ONLY whether the LLM backend itself fell back to
        # rule_fallback for routing/planning/summarizing — NOT whether some
        # unrelated data source degraded (e.g. no AMAP key -> local
        # gazetteer, or NDVI defaulting to synthetic_demo, which happens on
        # almost every NDVI query regardless of LLM health). Conflating the
        # two previously meant a fully-healthy DeepSeek run would still get
        # mislabeled "agent_rule_fallback" the moment it touched NDVI.
        llm_degraded = intent.degraded
        llm_degraded_notes: list[str] = [intent.degraded_reason] if intent.degraded_reason else []
        data_degraded = False
        data_degraded_notes: list[str] = []

        task_results: list[TaskResult] = []
        rag_sources: list[dict[str, Any]] = []
        used_rag = False
        plan: Optional[TaskPlan] = None
        answer = ""

        try:
            if intent.kind == "chitchat":
                sm.transition(AgentState.SUMMARIZING, "闲聊/无法匹配已知能力，直接用意图解析阶段的文本回答")
                answer = intent.content or "抱歉，我没能理解这个问题。"

            elif intent.kind == "rag":
                sm.transition(AgentState.RAG_RETRIEVAL, f"概念性问题，检索知识库：{intent.rag_topic}")
                chunks = retrieve(intent.rag_topic or query, top_k=self.settings.rag_top_k)
                rag_sources = [{"doc_id": c.doc_id, "doc_title": c.doc_title, "heading": c.heading, "score": round(c.score, 3)} for c in chunks]
                used_rag = True
                trace.emit(AgentEvent(state=AgentState.RAG_RETRIEVAL, kind="note", message=f"命中 {len(chunks)} 个知识库片段", payload={"sources": rag_sources}))

                sm.transition(AgentState.SUMMARIZING, "基于检索到的知识库内容生成回答")
                rag_context = [{"heading": c.heading, "text": c.text} for c in chunks]
                resp = await self.brain.summarize(query, [], rag_context, conversation_context)
                answer = resp.content or "抱歉，知识库中没有找到相关内容。"
                if resp.degraded:
                    llm_degraded = True
                    llm_degraded_notes.append(resp.degraded_reason)

            elif intent.kind == "direct_tool":
                sm.transition(AgentState.EXECUTING, f"单步直接调用工具：{intent.tool_call.name}")
                # Same session-scoped ToolResultCache the task_plan path uses
                # via Subagent.execute() (base.py) — this is the single
                # most common query shape (README scenario 1: "帮我找最近的
                # 自习室"), so it must not bypass the cache the way it
                # previously did.
                tool_name, tool_args = intent.tool_call.name, intent.tool_call.arguments
                cached_result = tool_cache.get(tool_name, tool_args)
                if cached_result is not None:
                    record = ToolCallRecord(tool_name=tool_name, arguments=tool_args, ok=True, result=cached_result, cache_hit=True)
                else:
                    record = await self.tool_registry.call(tool_name, tool_args)
                    if record.ok:
                        tool_cache.set(tool_name, tool_args, record.result)
                trace.emit(AgentEvent(
                    state=AgentState.EXECUTING, kind="tool_call",
                    message=f"{intent.tool_call.name}({intent.tool_call.arguments}) -> ok={record.ok}" + ("（缓存命中）" if record.cache_hit else ""),
                    payload={"tool": intent.tool_call.name, "ok": record.ok, "cache_hit": record.cache_hit},
                ))
                semantic_ok = record.result.get("ok", True) if isinstance(record.result, dict) else True
                task_status = TaskStatus.SUCCEEDED if (record.ok and semantic_ok) else TaskStatus.FAILED
                task_error = record.error or (record.result.get("message") if isinstance(record.result, dict) and not semantic_ok else None)
                task_results = [TaskResult(
                    task_id="direct", subagent="data_retrieval", goal=query,
                    status=task_status,
                    summary="直接工具调用" + ("成功" if task_status == TaskStatus.SUCCEEDED else "失败"),
                    data=record.result or {}, tool_calls=[record], error=task_error,
                    degraded=record.degraded, degraded_reason=record.degraded_reason,
                )]
                if record.degraded:  # tool/data-source degradation (e.g. no AMAP key) -> data tier, NOT llm tier
                    data_degraded = True
                    data_degraded_notes.append(record.degraded_reason)

                sm.transition(AgentState.VALIDATING, "校验单步工具调用结果")
                task_results = await self._validate_and_retry(task_results, sm, trace, tool_cache)

                sm.transition(AgentState.SUMMARIZING, "汇总单步工具调用结果")
                resp = await self.brain.summarize(query, [tr.model_dump(mode="json") for tr in task_results], [], conversation_context)
                answer = resp.content or "已完成查询，但未能生成文字说明。"
                if resp.degraded:
                    llm_degraded = True
                    llm_degraded_notes.append(resp.degraded_reason)

            else:  # task_plan
                plan = intent.task_plan
                sm.transition(AgentState.TASK_PLANNING, f"生成任务计划，共 {len(plan.tasks)} 个子任务")
                trace.emit(AgentEvent(state=AgentState.TASK_PLANNING, kind="note", message="任务计划", payload={"tasks": [t.model_dump() for t in plan.tasks]}))

                sm.transition(AgentState.EXECUTING, "按依赖顺序分发子任务给对应 Subagent")
                task_results = await self._execute_plan(plan, sm, trace, tool_cache, conversation_context)

                sm.transition(AgentState.VALIDATING, "校验全部子任务结果")
                task_results, plan = await self._validate_plan_results(task_results, plan, sm, trace, tool_cache, conversation_context)

                sm.transition(AgentState.SUMMARIZING, "汇总全部子任务结果")
                resp = await self.brain.summarize(query, [tr.model_dump(mode="json") for tr in task_results], [], conversation_context)
                answer = resp.content or "已完成多步查询，但未能生成文字说明。"
                if resp.degraded:
                    llm_degraded = True
                    llm_degraded_notes.append(resp.degraded_reason)

            sm.transition(AgentState.DONE, "生成最终回答")

        except Exception as exc:  # noqa: BLE001 - the whole point of ERROR state is to never 500 the endpoint
            sm.transition(AgentState.ERROR, f"未预期的异常：{exc.__class__.__name__}: {exc}")
            sm.transition(AgentState.SUMMARIZING, "异常后降级生成回答")
            answer = f"处理过程中出现异常，已尽量保留部分结果。错误信息：{exc}"
            sm.transition(AgentState.DONE, "异常路径结束")
            # An unhandled orchestrator-level exception is the Agent Loop
            # itself derailing (not merely one data source falling back to
            # a secondary source), so this counts as llm/system-tier —
            # consistent with `mode` meaning "did the intended, uninterrupted
            # path complete".
            llm_degraded = True
            llm_degraded_notes.append(f"orchestrator exception: {exc}")

        for tr in task_results:
            if tr.degraded and tr.degraded_reason:
                data_degraded = True
                data_degraded_notes.append(tr.degraded_reason)

        all_notes = llm_degraded_notes + data_degraded_notes
        if (llm_degraded or data_degraded) and all_notes:
            note = " | ".join(dict.fromkeys(n for n in all_notes if n))  # dedupe, keep order
            trace.emit(AgentEvent(state=AgentState.DONE, kind="note", message=f"本次回答存在降级：{note}"))

        conv.add_turn("assistant", answer)
        await conv.maybe_summarize(summarizer=self.brain.condense_history)
        self._maybe_learn_preference(user_id, query)

        chart = self._extract_chart(task_results)
        map_focus = self._extract_map_focus(task_results)

        return AgentChatResponse(
            session_id=session_id,
            query=query,
            answer=answer,
            mode="agent_rule_fallback" if llm_degraded else "agent_llm",
            data_degraded=data_degraded,
            data_degraded_notes=list(dict.fromkeys(n for n in data_degraded_notes if n)),
            used_rag=used_rag,
            rag_sources=rag_sources,
            task_plan=plan,
            task_results=task_results,
            chart=chart,
            map_focus=map_focus,
            events=trace.events,
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )

    # -- plan execution -------------------------------------------------------

    async def _execute_plan(self, plan: TaskPlan, sm: StateMachine, trace: RunTrace, tool_cache, conversation_context) -> list[TaskResult]:
        ordered = plan.topological_order()
        results_by_id: dict[str, TaskResult] = {}

        for task in ordered:
            subagent_cls = SUBAGENT_CLASSES.get(task.subagent)
            if subagent_cls is None:
                results_by_id[task.id] = TaskResult(
                    task_id=task.id, subagent=task.subagent, goal=task.goal,
                    status=TaskStatus.FAILED, summary="未知的 subagent 类型", error=f"unknown subagent: {task.subagent}",
                )
                continue

            subagent = subagent_cls(self.tool_registry, self.brain)
            skill_spec = self._skill_notes_for_subagent(task.subagent)
            upstream = [results_by_id[d] for d in task.depends_on if d in results_by_id]

            trace.emit(AgentEvent(state=AgentState.EXECUTING, kind="subagent_dispatch", message=f"派发任务 {task.id} 给 {task.subagent}", payload={"task_id": task.id, "goal": task.goal}))
            result = await subagent.execute(task, upstream, skill_spec, conversation_context, tool_cache=tool_cache)
            trace.emit(AgentEvent(
                state=AgentState.EXECUTING, kind="tool_call",
                message=f"任务 {task.id} -> {result.status.value}: {result.summary}",
                payload={"task_id": task.id, "status": result.status.value},
            ))
            results_by_id[task.id] = result

        return [results_by_id[t.id] for t in ordered]

    def _skill_notes_for_subagent(self, subagent_name: str) -> str:
        """A Subagent's tools may span multiple Skill domains (see
        subagents/spatial_analysis.py's docstring) — concatenate usage notes
        from every Skill whose tools overlap this Subagent's allowed set."""
        subagent_cls = SUBAGENT_CLASSES.get(subagent_name)
        if subagent_cls is None:
            return ""
        notes = []
        for skill in self.skill_registry.list_summaries():
            if any(t in subagent_cls.allowed_tools for t in skill.tools):
                full = self.skill_registry.load_full_spec(skill.name)
                if full:
                    notes.append(full.usage_notes)
        return "\n".join(notes)

    # -- validation + retry (direct_tool path) --------------------------------

    async def _validate_and_retry(self, task_results: list[TaskResult], sm: StateMachine, trace: RunTrace, tool_cache) -> list[TaskResult]:
        result = task_results[0]
        if result.status != TaskStatus.SUCCEEDED or not result.tool_calls:
            return task_results
        record = result.tool_calls[0]
        if record.tool_name in _EMPTY_RETRYABLE_TOOLS and isinstance(record.result, dict) and record.result.get("count") == 0:
            sm.transition(AgentState.EXECUTING, f"结果为空，放大搜索半径重试 {record.tool_name}")
            new_args = dict(record.arguments)
            new_args["radius"] = float(new_args.get("radius", 500)) * 2
            retried = await self.tool_registry.call(record.tool_name, new_args)
            trace.emit(AgentEvent(state=AgentState.EXECUTING, kind="tool_call", message=f"重试 {record.tool_name}，半径扩大至 {new_args['radius']}，count={retried.result.get('count') if retried.result else 'n/a'}"))
            sm.transition(AgentState.VALIDATING, "重试后再次校验")
            if retried.ok:
                task_results = [TaskResult(
                    task_id=result.task_id, subagent=result.subagent, goal=result.goal,
                    status=TaskStatus.SUCCEEDED, summary="放大半径重试后完成",
                    data=retried.result or {}, tool_calls=[record, retried], attempt=2,
                )]
        return task_results

    # -- validation + replanning (task_plan path) ------------------------------

    async def _validate_plan_results(
        self, task_results: list[TaskResult], plan: TaskPlan, sm: StateMachine, trace: RunTrace, tool_cache, conversation_context,
    ) -> tuple[list[TaskResult], TaskPlan]:
        results_by_id = {r.task_id: r for r in task_results}

        # 1) empty-result retry, same as the direct_tool path but per-task
        for task in plan.tasks:
            result = results_by_id.get(task.id)
            if result is None or result.status != TaskStatus.SUCCEEDED or not result.tool_calls:
                continue
            record = result.tool_calls[0]
            if record.tool_name in _EMPTY_RETRYABLE_TOOLS and isinstance(record.result, dict) and record.result.get("count") == 0:
                sm.transition(AgentState.EXECUTING, f"任务 {task.id} 结果为空，放大半径重试")
                new_args = dict(record.arguments)
                new_args["radius"] = float(new_args.get("radius", 500)) * 2
                retried = await self.tool_registry.call(record.tool_name, new_args)
                sm.transition(AgentState.VALIDATING, f"任务 {task.id} 重试后再次校验")
                if retried.ok:
                    results_by_id[task.id] = TaskResult(
                        task_id=task.id, subagent=result.subagent, goal=result.goal,
                        status=TaskStatus.SUCCEEDED, summary="放大半径重试后完成",
                        data=retried.result or {}, tool_calls=[record, retried], attempt=2,
                    )

        # 2) missing-dependency replanning: a failed task with a downstream
        #    dependent gets patched with one bounded repair task, up to
        #    settings.max_replans times total for this run.
        replans_done = 0
        repaired_ids: set[str] = set()
        failed_with_dependents = self._failed_tasks_with_dependents(plan, results_by_id, exclude_ids=repaired_ids)
        while failed_with_dependents and replans_done < self.settings.max_replans:
            failed_task = failed_with_dependents[0]
            repaired_ids.add(failed_task.id)
            sm.transition(AgentState.REPLANNING, f"任务 {failed_task.id} 失败且有下游依赖，插入补救的数据检索任务")
            repair_id = f"{failed_task.id}_repair"

            # Deterministic repair, NOT another brain.choose_tool() round: the
            # failed task already tried geocode's own AMap+gazetteer fallback
            # and still came up empty, so asking the same tool-selection logic
            # to "try again, but use a fallback point" would just re-select
            # geocode with the same unresolvable text and fail identically.
            # The orchestrator substitutes the known campus reference point
            # directly — a system-level repair decision, not a re-guess.
            from agent.llm_client import _CAMPUS_CENTER

            repair_record_result = {
                "ok": True, "source": "orchestrator_repair_fallback", "query": failed_task.goal,
                "matched_address": "紫金港校区参考点（基础图书馆，因原地址无法解析而使用的补救默认值）",
                "lat": _CAMPUS_CENTER["lat"], "lon": _CAMPUS_CENTER["lon"],
            }
            repair_result = TaskResult(
                task_id=repair_id, subagent="data_retrieval", goal=f"（补救）改用校园参考点代替无法解析的地址",
                status=TaskStatus.SUCCEEDED, summary="原地址无法解析，已改用校园参考点坐标继续后续步骤",
                data=repair_record_result, tool_calls=[],
                degraded=True, degraded_reason=f"原任务 {failed_task.id}（{failed_task.goal}）地址解析失败，已用校园参考点坐标代替，结果可能不准确",
            )
            trace.emit(AgentEvent(state=AgentState.REPLANNING, kind="note", message=f"新增补救任务 {repair_id}：改用校园参考点坐标", payload={"lat": _CAMPUS_CENTER["lat"], "lon": _CAMPUS_CENTER["lon"]}))
            results_by_id[repair_id] = repair_result

            sm.transition(AgentState.EXECUTING, f"重新执行依赖 {failed_task.id} 的下游任务")
            for dependent in plan.tasks:
                if failed_task.id in dependent.depends_on:
                    patched_deps = [d if d != failed_task.id else repair_id for d in dependent.depends_on]
                    upstream = [results_by_id[d] for d in patched_deps if d in results_by_id]
                    subagent_cls = SUBAGENT_CLASSES.get(dependent.subagent, SUBAGENT_CLASSES["data_retrieval"])
                    dep_subagent = subagent_cls(self.tool_registry, self.brain)
                    results_by_id[dependent.id] = await dep_subagent.execute(dependent, upstream, "", conversation_context, tool_cache=tool_cache)

            sm.transition(AgentState.VALIDATING, "补救后再次校验")
            replans_done += 1
            failed_with_dependents = self._failed_tasks_with_dependents(plan, results_by_id, exclude_ids=repaired_ids)

        ordered_ids = [t.id for t in plan.topological_order()]
        final_results = [results_by_id[tid] for tid in ordered_ids if tid in results_by_id]
        # include repair tasks at the end so they're visible in the trace/response
        for tid, r in results_by_id.items():
            if tid not in ordered_ids:
                final_results.append(r)
        return final_results, plan

    @staticmethod
    def _failed_tasks_with_dependents(plan: TaskPlan, results_by_id: dict[str, TaskResult], exclude_ids: set[str] | None = None) -> list[TaskSpec]:
        exclude_ids = exclude_ids or set()
        dependent_ids = {dep for t in plan.tasks for dep in t.depends_on}
        return [
            t for t in plan.tasks
            if t.id in dependent_ids and t.id not in exclude_ids
            and results_by_id.get(t.id) is not None and results_by_id[t.id].status == TaskStatus.FAILED
        ]

    # -- long-term memory (very light-touch heuristic) -------------------------

    def _maybe_learn_preference(self, user_id: str, query: str) -> None:
        """Deliberately simple, deterministic heuristic (not an LLM call) —
        this project's point isn't sophisticated preference inference, it's
        demonstrating that long-term memory genuinely persists and gets
        re-injected on the next session (see docs/AGENT_ARCHITECTURE.md)."""
        if "安静" in query:
            self.long_term.upsert_preference(user_id, "noise_preference", "偏好安静的自习室")
        if "充电" in query or "插座" in query:
            self.long_term.upsert_preference(user_id, "power_preference", "偏好靠近充电桩/插座的位置")

    # -- response extraction helpers -------------------------------------------

    @staticmethod
    def _extract_chart(task_results: list[TaskResult]) -> Optional[dict[str, Any]]:
        for r in task_results:
            if r.data.get("url_path"):
                return {"url_path": r.data["url_path"], "thumbnail_base64": r.data.get("thumbnail_base64"), "title": r.data.get("title")}
        return None

    @staticmethod
    def _extract_map_focus(task_results: list[TaskResult]) -> list[dict[str, Any]]:
        focus = []
        for r in task_results:
            for item in r.data.get("results", [])[:10]:
                if "lat" in item and "lon" in item:
                    focus.append({"name": item.get("name"), "lat": item["lat"], "lon": item["lon"]})
            if "lat" in r.data and "lon" in r.data:
                focus.append({"name": r.data.get("matched_address", r.goal), "lat": r.data["lat"], "lon": r.data["lon"]})
        return focus


_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator
