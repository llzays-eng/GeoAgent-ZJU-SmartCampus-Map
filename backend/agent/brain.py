"""agent/brain.py — High-level cognitive operations for the Agent Loop.

Everything here is written once, against the `ChatBackend` protocol
(agent/llm_client.py) — it never imports DeepSeek or rule-engine specifics
directly. This is what makes "real function calling + rule-based fallback,
completely swappable" actually true rather than aspirational: `Brain` calls
`backend.chat(messages, tools, tool_choice)` and only ever looks at the
returned `LLMResponse.tool_calls` / `.content` — identical code path whether
`backend` is `DeepSeekChatBackend` or `RuleFallbackChatBackend`.

Two meta-tools are defined here (not in agent/tools/registry.py) because
they are Orchestrator-level *routing* decisions, never executed by
ToolRegistry:
  create_task_plan          — signals "this needs multi-step planning";
                               its argument IS the TaskPlan.
  answer_from_knowledge_base — signals "this is a definitional/explanatory
                               question, go to RAG_RETRIEVAL", carrying the
                               topic to search for.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Optional

from agent.llm_client import ChatBackend, LLMResponse, ToolCall
from agent.schemas import TaskPlan, TaskSpec

CREATE_TASK_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "create_task_plan",
        "description": (
            "当问题需要多个步骤/多个能力域协作才能回答时调用（例如：先查数据再生成图表，"
            "或需要对比多个候选点）。参数是一份结构化任务列表，每个任务标明目标、"
            "负责执行的 Subagent，以及依赖的前置任务id。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "goal": {"type": "string"},
                            "subagent": {"type": "string", "enum": ["data_retrieval", "spatial_analysis", "reporting"]},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["id", "goal", "subagent"],
                    },
                }
            },
            "required": ["tasks"],
        },
    },
}

ANSWER_FROM_KB_TOOL = {
    "type": "function",
    "function": {
        "name": "answer_from_knowledge_base",
        "description": (
            "当问题是概念性/定义性的（例如“NDVI是什么意思”“R-tree和线性扫描有什么区别”），"
            "需要从项目知识库检索方法论说明来回答时调用，而不是执行数据查询工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {"topic": {"type": "string", "description": "要检索的概念/问题"}},
            "required": ["topic"],
        },
    },
}


@dataclass
class IntentResult:
    kind: Literal["direct_tool", "task_plan", "rag", "chitchat"]
    tool_call: Optional[ToolCall] = None
    task_plan: Optional[TaskPlan] = None
    rag_topic: Optional[str] = None
    content: Optional[str] = None
    degraded: bool = False
    degraded_reason: str = ""


INTENT_SYSTEM_PROMPT = """\
你是紫金港校区 GeoAgent 的意图解析模块。你可以：
1. 直接调用下列某个数据工具（问题单一、一步就能回答时）；
2. 调用 create_task_plan，把问题拆解为多个子任务分别交给不同 Subagent（问题涉及多个能力域，
   或需要先查数据再处理/可视化时）；
3. 调用 answer_from_knowledge_base（问题是定义性/概念性的，不需要查询实时数据时）；
4. 都不调用，直接用 content 文本回答（纯闲聊或问题与校园数据完全无关时）。

以下是当前可用的能力域一览（仅摘要，完整参数说明在被选中后才会加载）：
{skill_menu}

只依据用户实际问出的信息行动，不要编造用户没有提供的地点/年份等参数；
缺参数时，为直接工具调用选一个合理默认值（比如没提到具体地点就使用校园中心参考点），
并在后续汇总时说明用了默认假设。
"""


class Brain:
    def __init__(self, backend: ChatBackend):
        self.backend = backend

    # -- intent parsing (state: INTENT_PARSING) ------------------------------

    async def parse_intent(
        self,
        query: str,
        skill_menu: str,
        direct_tool_schemas: list[dict[str, Any]],
        conversation_context: list[dict[str, str]],
    ) -> IntentResult:
        system_prompt = INTENT_SYSTEM_PROMPT.format(skill_menu=skill_menu)
        messages = [{"role": "system", "content": system_prompt}, *conversation_context, {"role": "user", "content": query}]
        tools = [*direct_tool_schemas, CREATE_TASK_PLAN_TOOL, ANSWER_FROM_KB_TOOL]

        response = await self.backend.chat(messages, tools=tools, tool_choice="auto")

        if not response.tool_calls:
            return IntentResult(kind="chitchat", content=response.content, degraded=response.degraded, degraded_reason=response.degraded_reason)

        call = response.tool_calls[0]
        if call.name == "create_task_plan":
            try:
                tasks = [TaskSpec(**t) for t in call.arguments.get("tasks", [])]
                plan = TaskPlan(tasks=tasks)
                plan.topological_order()  # validate now — surface a bad plan immediately, not mid-execution
            except Exception as exc:
                # Malformed plan from the LLM — degrade to a single-task plan
                # around whatever the raw goal text was, rather than crashing.
                plan = TaskPlan(tasks=[TaskSpec(id="t1", goal=query, subagent="data_retrieval", depends_on=[])])
                return IntentResult(kind="task_plan", task_plan=plan, degraded=True, degraded_reason=f"计划解析失败({exc})，已降级为单任务计划")
            return IntentResult(kind="task_plan", task_plan=plan, degraded=response.degraded, degraded_reason=response.degraded_reason)

        if call.name == "answer_from_knowledge_base":
            return IntentResult(kind="rag", rag_topic=call.arguments.get("topic", query), degraded=response.degraded, degraded_reason=response.degraded_reason)

        return IntentResult(kind="direct_tool", tool_call=call, degraded=response.degraded, degraded_reason=response.degraded_reason)

    # -- subagent tool choice (state: EXECUTING) -----------------------------

    async def choose_tool(
        self,
        subagent_name: str,
        goal: str,
        tool_schemas: list[dict[str, Any]],
        skill_usage_notes: str,
        conversation_context: list[dict[str, str]],
    ) -> Optional[ToolCall]:
        system_prompt = (
            f"你是 {subagent_name} Subagent，只能从提供给你的工具集合中选择，不能调用其他工具。\n"
            f"{skill_usage_notes}"
        )
        messages = [{"role": "system", "content": system_prompt}, *conversation_context, {"role": "user", "content": goal}]
        response = await self.backend.chat(messages, tools=tool_schemas, tool_choice="auto")
        return response.tool_calls[0] if response.tool_calls else None

    # -- summarization (state: SUMMARIZING) ----------------------------------

    async def summarize(
        self,
        query: str,
        task_results: list[dict[str, Any]],
        rag_chunks: list[dict[str, Any]],
        conversation_context: list[dict[str, str]],
    ) -> LLMResponse:
        context = {"purpose": "summarize", "query": query, "task_results": task_results, "rag_chunks": rag_chunks}
        user_message = self._with_context(
            f"请根据以上工具执行结果，用自然语言回答用户的问题：{query}\n"
            "如果某个数据来源是模拟/降级数据（data_source=synthetic_demo 或 _degraded_reason 非空），"
            "必须在回答中明确说明，不能当作真实数据呈现。",
            context,
        )
        messages = [
            {"role": "system", "content": "你是紫金港校区 GeoAgent 的汇总模块，只依据提供的结果作答，不要编造未出现过的数字或地点。"},
            *conversation_context,
            {"role": "user", "content": user_message},
        ]
        return await self.backend.chat(messages, tools=None)

    # -- short-term memory condensation --------------------------------------

    async def condense_history(self, turns: list[dict[str, str]]) -> str:
        context = {"purpose": "condense_history", "turns": turns}
        user_message = self._with_context("请把以上历史对话浓缩成几句话摘要，保留关键事实（用户提到的地点/偏好/已给出的结论）。", context)
        messages = [
            {"role": "system", "content": "你负责压缩长对话历史，只保留关键信息，不要展开新的分析。"},
            {"role": "user", "content": user_message},
        ]
        response = await self.backend.chat(messages, tools=None)
        return response.content or ""

    @staticmethod
    def _with_context(instruction: str, context: dict[str, Any]) -> str:
        """Append a fenced JSON block the rule-fallback backend can parse
        back out deterministically (see llm_client._extract_structured_context).
        A real LLM just reads it as JSON-formatted context, which is a
        completely ordinary thing to put in a prompt."""
        return f"{instruction}\n\n```json\n{json.dumps(context, ensure_ascii=False, default=str)}\n```"


_brain: Brain | None = None


def get_brain() -> Brain:
    global _brain
    if _brain is None:
        from agent.llm_client import get_chat_backend

        _brain = Brain(get_chat_backend())
    return _brain


def reset_brain_cache() -> None:
    global _brain
    _brain = None
