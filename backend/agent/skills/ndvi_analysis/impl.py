from agent.skills.registry import SkillFullSpec

USAGE_NOTES = """\
ndvi_analysis 技能覆盖 ndvi_trend 工具。

- 年份范围建议不超过35年（工具会自动截断），这是"精简版"设计的一部分，
  不追求完整 RSEI-CLCD 全流程。
- 返回结果一定带 data_source 字段。data_source=synthetic_demo 时，必须在
  面向用户的回答里明确说明这是本地模拟数据、不是真实遥感反演结果——
  这是强制要求，不是可选提示。
- trend 字段只是首尾年份的简单比较，不构成统计显著性结论；如果用户问
  "趋势是否显著"，应如实说明该工具不做显著性检验。
"""

EXAMPLES = [
    {
        "user_intent": "过去5年这片区域的植被恢复情况怎么样，做个图表给我看",
        "tool_sequence": [
            {"tool": "ndvi_trend", "arguments": {"region": "紫金港校区", "start_year": 2020, "end_year": 2024}},
            {"tool": "generate_chart", "arguments": {"data": "<上一步 series>", "chart_type": "line", "title": "NDVI趋势 2020-2024"}},
        ],
    },
]


def load_full_spec() -> SkillFullSpec:
    return SkillFullSpec(name="ndvi_analysis", usage_notes=USAGE_NOTES, examples=EXAMPLES)
