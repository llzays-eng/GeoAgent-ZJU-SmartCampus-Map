from agent.skills.registry import SkillFullSpec

USAGE_NOTES = """\
report_generation 技能覆盖 generate_chart 工具。

- data 参数接受 {label: value} 映射，或 [{label, value}, ...] 数组；
  NDVI 结果的 series（[{year, ndvi_mean}, ...]）可以直接传入，字段名
  会被自动识别，不需要手动改字段名。
- chart_type 三选一：数量对比用 bar，时间序列趋势用 line，占比用 pie。
- 调用成功后返回 url_path（可通过后端静态资源访问）和
  thumbnail_base64（可直接内嵌展示），两者任选其一展示给用户即可，
  不需要都用。
"""

EXAMPLES = [
    {
        "user_intent": "把NDVI趋势做成图表",
        "tool_sequence": [
            {"tool": "generate_chart", "arguments": {"data": [{"year": 2020, "ndvi_mean": 0.5}], "chart_type": "line"}},
        ],
    },
]


def load_full_spec() -> SkillFullSpec:
    return SkillFullSpec(name="report_generation", usage_notes=USAGE_NOTES, examples=EXAMPLES)
