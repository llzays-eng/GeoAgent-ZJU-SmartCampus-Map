"""agent/llm_client.py — Swappable chat backends behind one interface.

Outline section 5 requires *real* function calling — the LLM API's actual
`tools` / `tool_calls` mechanism, not "prompt the model to emit JSON and
regex it out" (which is what the original project's single `call_deepseek()`
did). `DeepSeekChatBackend` is that: an OpenAI-compatible function-calling
call against DeepSeek.

But this project needed a way to build AND TEST the entire orchestration
logic (intent routing, task planning, subagent tool selection, replanning)
inside a sandbox with no access to api.deepseek.com. `RuleFallbackChatBackend`
is the answer: it implements the *exact same* `ChatBackend.chat()` signature
— same `messages`/`tools`/`tool_choice` in, same `LLMResponse` (with
`tool_calls`) out — using deterministic keyword rules instead of a model.
Anything that consumes a `ChatBackend` (see brain.py) genuinely cannot tell
the two apart from the interface, only from behaviour.

This mirrors — deliberately — the project's *existing* fallback philosophy
(`fallback_recommend()` in main.py already degrades to keyword scoring when
no DeepSeek key is set). Here that same idea is generalized to the whole
Agent Loop: `AutoFallbackChatBackend` tries the real API and drops to the
rule engine per-call if it errors, so a transient DeepSeek outage degrades
the answer quality but never crashes the conversation.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from agent.config import get_settings
from agent.tools.geocode_tool import _local_gazetteer  # reuse, don't re-implement


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    degraded: bool = False
    degraded_reason: str = ""


class ChatBackend(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.2,
    ) -> LLMResponse: ...


# ── Real backend: DeepSeek function calling ────────────────────────────────

class DeepSeekChatBackend:
    """OpenAI-compatible function calling against DeepSeek's API. This is
    what the Orchestrator/Subagents use when AGENT_LLM_BACKEND=deepseek and
    DEEPSEEK_API_KEY is set. Could not be exercised end-to-end in the build
    sandbox (api.deepseek.com isn't in the network allowlist here) — it is a
    real implementation for the user's own deployment, following the same
    request shape as the original project's call_deepseek(), extended with
    `tools`."""

    def __init__(self, api_key: str, base_url: str, model: str):
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def chat(self, messages, tools=None, tool_choice="auto", temperature=0.2) -> LLMResponse:
        kwargs: dict[str, Any] = {"model": self._model, "messages": messages, "temperature": temperature}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        response = await self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message

        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        return LLMResponse(content=message.content, tool_calls=tool_calls)


# ── Fallback backend: deterministic rule engine ────────────────────────────
#
# NOT an attempt to reimplement NLU. This is intentionally simple — it only
# needs to exercise every branch of the state machine (simple tool call /
# multi-task plan / RAG routing / replanning) for demos, dev, and this
# project's own tests. Real query understanding is the DeepSeek backend's
# job; see docs/AGENT_ARCHITECTURE.md "已实现 vs 简化/模拟" for the explicit
# list of what is genuinely LLM-driven vs rule-driven.

_CAMPUS_CENTER = {"lat": 30.3061194, "lon": 120.083702}  # 基础图书馆, used as a
# sensible default reference point when the rule engine can't identify a
# specific place in the query — a real LLM would ask or use user location;
# a documented default is the honest rule-based equivalent.

_KEYWORD_CATEGORIES = {
    "find_room": ["自习室", "座位", "自习", "上自习"],
    "charging": ["充电", "插座", "电源", "充电桩"],
    "ndvi": ["NDVI", "植被", "绿化", "生态指数", "RSEI"],
    "chart": ["图表", "可视化", "画个图", "趋势图", "柱状图", "折线图"],
    "compare_multi": ["对比", "比较", "哪个更好", "选址", "综合评估", "候选"],
    "locate": ["在哪里", "在哪", "地址是", "位置在", "位置是"],
    "poi_general": ["附近", "食堂", "图书馆", "教学楼", "餐厅", "怎么走"],
}

_LOCATE_STRIP_SUFFIXES = ["在哪里", "在哪", "的地址是什么", "地址是什么", "地址是", "的位置在哪", "位置在哪", "位置在", "位置是"]

_KNOWLEDGE_PATTERNS = [
    re.compile(r".*是什么意思"),
    re.compile(r".*是什么(?!地方|地点)"),
    re.compile(r".*什么叫"),
    re.compile(r".*为什么(不能|不可以)"),
    re.compile(r".*原理"),
    re.compile(r".*区别"),
]
_KNOWLEDGE_DOMAIN_TERMS = ["NDVI", "RSEI", "haversine", "Haversine", "坐标系", "R-tree", "rtree", "线性扫描", "最近邻", "缓冲区", "PCA", "GCJ", "BD-09", "WGS"]


def _last_user_text(messages: list[dict[str, str]]) -> str:
    for msg in reversed(messages):
        if msg["role"] == "user":
            return msg["content"]
    return ""


def _extract_structured_context(text: str) -> dict | None:
    """Brain always appends a fenced ```json block to the user message when
    it needs the rule engine to operate on structured data (task results,
    retrieved chunks) instead of free natural language — see brain.py's
    `_with_context()`. This is the matching parser."""
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


class RuleFallbackChatBackend:
    async def chat(self, messages, tools=None, tool_choice="auto", temperature=0.2) -> LLMResponse:
        tool_names = {t["function"]["name"] for t in (tools or [])}
        user_text = _last_user_text(messages)

        if "create_task_plan" in tool_names:
            return self._route_top_level(user_text, tool_names)
        if tool_names:
            return self._choose_from_constrained(user_text, tools or [])
        return self._generate_text(messages, user_text)

    # -- top-level intent routing (INTENT_PARSING state) --------------------

    def _matched_categories(self, text: str) -> list[str]:
        return [cat for cat, kws in _KEYWORD_CATEGORIES.items() if any(kw in text for kw in kws)]

    def _looks_like_knowledge_question(self, text: str) -> bool:
        has_domain_term = any(term.lower() in text.lower() for term in _KNOWLEDGE_DOMAIN_TERMS)
        has_pattern = any(p.match(text) for p in _KNOWLEDGE_PATTERNS)
        return has_domain_term and has_pattern

    def _resolve_center(self, text: str) -> dict[str, float]:
        for name, (lat, lon) in _local_gazetteer().items():
            if name in text:
                return {"lat": lat, "lon": lon}
        return dict(_CAMPUS_CENTER)

    def _route_top_level(self, text: str, tool_names: set[str]) -> LLMResponse:
        if self._looks_like_knowledge_question(text) and "answer_from_knowledge_base" in tool_names:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="rule_1", name="answer_from_knowledge_base", arguments={"topic": text})],
                degraded=True,
                degraded_reason="rule_fallback: 关键词命中知识性问题模式，路由到RAG检索",
            )

        categories = self._matched_categories(text)
        # "poi_general" is triggered by very common, non-distinctive words
        # (e.g. "附近") and should not by itself count as a second capability
        # domain requiring multi-task planning — otherwise "查一下附近的充电桩"
        # (charging + poi_general) incorrectly triggers a 2-task plan instead
        # of the single query_charging_pile call it actually needs. It only
        # matters when NOTHING more specific matched at all.
        specific_categories = [c for c in categories if c != "poi_general"]
        center = self._resolve_center(text)

        # Multiple distinct capability domains -> needs a real task plan.
        if len(specific_categories) >= 2 and "create_task_plan" in tool_names:
            tasks = self._build_task_plan(categories, center)
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="rule_1", name="create_task_plan", arguments={"tasks": tasks})],
                degraded=True,
                degraded_reason=f"rule_fallback: 命中多个能力域 {specific_categories}，生成任务计划",
            )

        if len(specific_categories) == 1:
            chosen_category = specific_categories[0]
        elif categories == ["poi_general"]:
            chosen_category = "poi_general"
        else:
            # Nothing matched at all -> treat as chit-chat, no tool call.
            return LLMResponse(
                content="这个问题我暂时无法匹配到具体的校园数据查询能力，可以换个更具体的说法吗？比如提到具体地点、自习室、充电桩或NDVI等关键词。",
                tool_calls=[],
                degraded=True,
                degraded_reason="rule_fallback: 未命中任何已知能力域关键词",
            )

        # Single clear category -> one direct tool call (simple path).
        direct_tool, args = self._single_tool_for_category(chosen_category, text, center)
        if direct_tool in tool_names:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="rule_1", name=direct_tool, arguments=args)],
                degraded=True,
                degraded_reason=f"rule_fallback: 命中单一能力域 '{chosen_category}'，直接调用 {direct_tool}",
            )

        return LLMResponse(
            content="这个问题我暂时无法匹配到具体的校园数据查询能力，可以换个更具体的说法吗？比如提到具体地点、自习室、充电桩或NDVI等关键词。",
            tool_calls=[],
            degraded=True,
            degraded_reason="rule_fallback: 匹配到的工具不在当前可用工具集中",
        )

    def _single_tool_for_category(self, category: str, text: str, center: dict) -> tuple[str, dict]:
        if category == "charging":
            return "query_charging_pile", {"location": center, "limit": 5}
        if category == "ndvi":
            years = re.findall(r"20\d{2}", text)
            start_year = int(years[0]) if years else 2019
            end_year = int(years[1]) if len(years) > 1 else 2024
            return "ndvi_trend", {"region": "紫金港校区", "start_year": start_year, "end_year": end_year}
        if category == "find_room":
            quiet = "安静" in text
            return "search_poi", {"center": center, "dataset": "study_rooms", "category": "quiet" if quiet else "", "radius": 1000, "limit": 8}
        if category == "locate":
            address = text
            for suffix in _LOCATE_STRIP_SUFFIXES:
                address = address.replace(suffix, "")
            return "geocode", {"address": address.strip() or text}
        # poi_general / chart / compare_multi (as a lone category) / default
        return "search_poi", {"center": center, "dataset": "pois", "radius": 800, "limit": 8}

    def _build_task_plan(self, categories: list[str], center: dict) -> list[dict]:
        tasks: list[dict] = []
        step = 1
        # Every task that PRODUCES chartable/comparable data this plan might
        # want downstream — a list, not a single overwritable scalar. The
        # previous single `data_task` variable got clobbered by whichever
        # data-producing category ran last (e.g. "ndvi" after "find_room"),
        # so a later "chart" task would only ever depend_on the LAST
        # producer, silently dropping earlier ones (e.g. a POI search) from
        # the dependency graph entirely — see docs/AGENT_ARCHITECTURE.md and
        # the review this fixes for the concrete repro.
        data_task_ids: list[str] = []

        def add(subagent: str, goal: str, depends_on: list[str] | None = None) -> str:
            nonlocal step
            tid = f"t{step}"
            step += 1
            tasks.append({"id": tid, "goal": goal, "subagent": subagent, "depends_on": depends_on or []})
            return tid

        if "find_room" in categories or "poi_general" in categories:
            data_task_ids.append(add("data_retrieval", f"在坐标附近({center['lat']:.4f},{center['lon']:.4f})搜索相关自习室/POI"))
        if "charging" in categories:
            # Charging-pile status is point-in-time, not itself chartable/
            # comparable data for a downstream task, so it deliberately does
            # NOT join data_task_ids — only mirrors the original behaviour
            # of depending on the POI/study-room search when one exists.
            add("data_retrieval", "查询附近充电桩状态", depends_on=[data_task_ids[-1]] if data_task_ids else [])
        if "ndvi" in categories:
            data_task_ids.append(add("spatial_analysis", "查询NDVI年度趋势（紫金港校区，近5年）"))
        if "compare_multi" in categories and not data_task_ids:
            data_task_ids.append(add("spatial_analysis", f"对坐标附近({center['lat']:.4f},{center['lon']:.4f})做缓冲区综合统计"))
        if "chart" in categories and data_task_ids:
            # Depend on EVERY data-producing task, not just the most
            # recently added one, so none of their outputs get silently
            # dropped from the graph.
            add("reporting", "将上一步的数据结果生成图表", depends_on=list(data_task_ids))

        if not tasks:  # safety net — should not happen given len(categories) >= 2, but never emit an empty plan
            tasks.append({"id": "t1", "goal": "搜索校园相关点位", "subagent": "data_retrieval", "depends_on": []})
        return tasks

    # -- subagent-level tool choice ------------------------------------------

    def _choose_from_constrained(self, goal_text: str, tools: list[dict]) -> LLMResponse:
        available = {t["function"]["name"] for t in tools}
        center = self._resolve_center(goal_text)
        primary_goal = goal_text.split("\n（前置任务参考信息", 1)[0]  # see base.py's enrichment format

        if len(available) == 1:
            # No real choice to make (e.g. ReportingSubagent only ever has
            # generate_chart) — category keyword matching has no entry for
            # every possible goal phrasing, so don't force it through that
            # path. Real chart *data* comes from base.py's dependency
            # threading, not from parsing this goal text.
            only_tool = next(iter(available))
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="rule_1", name=only_tool, arguments=self._default_args_for(only_tool, center, primary_goal))],
                degraded=True,
                degraded_reason=f"rule_fallback: subagent 只有一个可用工具，直接选用 {only_tool}",
            )

        categories = self._matched_categories(primary_goal)
        specific_categories = [c for c in categories if c != "poi_general"]
        if specific_categories:
            category = specific_categories[0]
        elif categories == ["poi_general"]:
            category = "poi_general"
        else:
            category = "poi_general"  # constrained subagent call always needs *some* tool; this is the generic default
        tool_name, args = self._single_tool_for_category(category, primary_goal, center)

        if tool_name not in available:
            tool_name = next(iter(available)) if available else ""
            args = self._default_args_for(tool_name, center, goal_text)
            if not tool_name:
                return LLMResponse(content="无可用工具", tool_calls=[], degraded=True, degraded_reason="rule_fallback: constrained tool list empty")

        return LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="rule_1", name=tool_name, arguments=args)],
            degraded=True,
            degraded_reason=f"rule_fallback: subagent 约束工具集内选择 {tool_name}",
        )

    def _default_args_for(self, tool_name: str, center: dict, text: str) -> dict:
        if tool_name in ("search_poi", "spatial_buffer"):
            return {"center": center, "radius": 800} if tool_name == "spatial_buffer" else {"center": center}
        if tool_name == "query_charging_pile":
            return {"location": center}
        if tool_name == "geocode":
            return {"address": text[:30]}
        if tool_name == "ndvi_trend":
            return {"region": "紫金港校区", "start_year": 2019, "end_year": 2024}
        if tool_name == "generate_chart":
            return {"data": [], "chart_type": "bar"}
        return {}

    # -- plain text generation (summarize / condense_history) ---------------

    def _generate_text(self, messages: list[dict[str, str]], user_text: str) -> LLMResponse:
        context = _extract_structured_context(user_text)
        if context is None:
            return LLMResponse(
                content="（rule_fallback：未在消息中找到结构化上下文，无法生成摘要）",
                degraded=True,
                degraded_reason="rule_fallback: 纯文本生成缺少结构化上下文，见 brain.py `_with_context`",
            )

        purpose = context.get("purpose", "summarize")
        if purpose == "condense_history":
            return LLMResponse(content=self._condense_history_text(context), degraded=True, degraded_reason="rule_fallback: 规则摘要历史对话")
        return LLMResponse(content=self._summarize_text(context), degraded=True, degraded_reason="rule_fallback: 规则模板汇总任务结果")

    @staticmethod
    def _condense_history_text(context: dict) -> str:
        turns = context.get("turns", [])
        bullets = []
        for t in turns:
            role = "用户" if t.get("role") == "user" else "助手"
            content = str(t.get("content", "")).strip().replace("\n", " ")[:60]
            bullets.append(f"- {role}: {content}")
        return "（历史对话摘要，rule_fallback生成）\n" + "\n".join(bullets)

    @staticmethod
    def _summarize_text(context: dict) -> str:
        parts = []
        query = context.get("query", "")
        parts.append(f"关于「{query}」，汇总如下（rule_fallback 模板生成，未使用真实LLM，措辞较生硬）：")

        for tr in context.get("task_results", []):
            status = tr.get("status")
            goal = tr.get("goal", "")
            if status != "succeeded":
                parts.append(f"- 子任务「{goal}」未成功完成（{tr.get('error', '原因未知')}），该部分结果暂缺。")
                continue
            data = tr.get("data", {})
            if "results" in data:
                names = [r.get("name") for r in data["results"][:5]]
                parts.append(f"- 「{goal}」找到 {data.get('count', len(names))} 个结果：{', '.join(names) if names else '无'}。")
            elif "series" in data:
                series = data["series"]
                disclaimer = data.get("disclaimer", "")
                trend_desc = f"，趋势：{data.get('trend')}" if data.get("trend") else ""
                parts.append(f"- 「{goal}」NDVI序列：{series}{trend_desc}。" + (f" {disclaimer}" if disclaimer else ""))
            elif "stations" in data:
                parts.append(f"- 「{goal}」充电桩查询：{'可用' if data.get('ok') else '暂不可用'}，返回 {len(data.get('stations', []))} 个站点。")
            elif "url_path" in data:
                parts.append(f"- 「{goal}」已生成图表：{data.get('url_path')}。")
            else:
                parts.append(f"- 「{goal}」已完成。")

        rag_chunks = context.get("rag_chunks", [])
        if rag_chunks:
            parts.append("补充知识库参考：" + "；".join(c.get("heading", "") for c in rag_chunks))

        return "\n".join(parts)


# ── Auto-degrading wrapper ──────────────────────────────────────────────────

class AutoFallbackChatBackend:
    """Tries the real backend; on ANY exception (network, auth, rate limit,
    malformed response) degrades to the rule engine *for that call only* and
    tags the response, rather than crashing the whole conversation. This is
    the per-call analogue of the project's existing "no API key -> fallback"
    behaviour, extended to handle "API key present but the call failed right
    now" too."""

    def __init__(self, primary: ChatBackend, fallback: ChatBackend):
        self._primary = primary
        self._fallback = fallback

    async def chat(self, messages, tools=None, tool_choice="auto", temperature=0.2) -> LLMResponse:
        try:
            return await self._primary.chat(messages, tools=tools, tool_choice=tool_choice, temperature=temperature)
        except Exception as exc:  # noqa: BLE001 - must degrade, never crash the agent loop
            response = await self._fallback.chat(messages, tools=tools, tool_choice=tool_choice, temperature=temperature)
            response.degraded = True
            response.degraded_reason = f"DeepSeek调用失败({exc.__class__.__name__}: {exc})，已降级为rule_fallback: {response.degraded_reason}"
            return response


_backend: ChatBackend | None = None


def get_chat_backend() -> ChatBackend:
    global _backend
    if _backend is not None:
        return _backend

    settings = get_settings()
    rule_backend = RuleFallbackChatBackend()

    if settings.llm_backend == "deepseek" and settings.deepseek_api_key:
        deepseek_backend = DeepSeekChatBackend(settings.deepseek_api_key, settings.deepseek_base_url, settings.deepseek_model)
        _backend = AutoFallbackChatBackend(primary=deepseek_backend, fallback=rule_backend)
    else:
        _backend = rule_backend
    return _backend


def reset_chat_backend_cache() -> None:
    """Test helper — settings are lru_cached, so tests that flip env vars
    need to force a rebuild."""
    global _backend
    _backend = None
