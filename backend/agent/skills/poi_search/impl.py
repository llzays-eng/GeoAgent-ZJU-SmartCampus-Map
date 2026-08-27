"""agent/skills/poi_search/impl.py — Tier-2 detail for the poi_search skill.

Only imported when SkillRegistry.load_full_spec("poi_search") is called,
i.e. only after the Orchestrator's planning step already decided this
skill is relevant. This keeps the always-resident prompt small (outline
section 7's whole point).
"""
from agent.skills.registry import SkillFullSpec

USAGE_NOTES = """\
poi_search 技能覆盖 3 个工具：search_poi / geocode / query_charging_pile。

- search_poi 的 center 参数必须是坐标 {lat, lon}；如果用户说的是地名
  （"基础图书馆附近"），必须先调用 geocode 把地名转换成坐标，不要让
  Subagent 自己猜坐标。
- category 留空表示不限类别；自习室的合法值是 quiet/discussion/overnight/
  library/unknown，POI 的合法值是 library/teaching/canteen/scenic/service/
  museum/other。
- query_charging_pile 在 ZJU-Charger 接口不可用时会返回 ok=false 和一个
  fallback_url，这不是错误，应如实告诉用户"充电桩数据暂不可用，可访问
  外链查看"，不要编造充电桩数量。
"""

EXAMPLES = [
    {
        "user_intent": "帮我找紫金港校区里离我最近、还有空位的自习室，优先选旁边有充电桩的",
        "tool_sequence": [
            {"tool": "search_poi", "arguments": {"center": {"lat": 30.3061, "lon": 120.0837}, "dataset": "study_rooms", "radius": 1000, "limit": 8}},
            {"tool": "query_charging_pile", "arguments": {"location": {"lat": 30.3061, "lon": 120.0837}, "radius": 300}},
        ],
    },
    {
        "user_intent": "北教学区附近有什么吃饭的地方",
        "tool_sequence": [
            {"tool": "geocode", "arguments": {"address": "北教学区"}},
            {"tool": "search_poi", "arguments": {"center": {"lat": "<上一步结果>", "lon": "<上一步结果>"}, "category": "canteen", "dataset": "pois"}},
        ],
    },
]


def load_full_spec() -> SkillFullSpec:
    return SkillFullSpec(name="poi_search", usage_notes=USAGE_NOTES, examples=EXAMPLES)
