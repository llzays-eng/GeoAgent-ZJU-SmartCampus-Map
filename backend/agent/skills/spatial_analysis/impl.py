from agent.skills.registry import SkillFullSpec

USAGE_NOTES = """\
spatial_analysis 技能覆盖 spatial_buffer 工具。

- radius 单位是米；不要把用户说的"附近""不远"直接编造成具体数字——
  没有明确距离时，默认用 500m，并在最终回答里说明这是默认假设。
- filter 参数是精确匹配（不是模糊匹配），键名必须是数据集里真实存在的
  属性名（noise_level / group_study / overnight_available / category 等），
  编造不存在的字段会导致过滤结果始终为空。
- 多候选地点对比时，对每个候选点分别调用一次 spatial_buffer，然后在
  汇总阶段比较 count 和关键属性分布，不要只调用一次就下结论。
"""

EXAMPLES = [
    {
        "user_intent": "如果要新开一个自习室，这三个候选地点里哪个综合条件最好？",
        "tool_sequence": [
            {"tool": "spatial_buffer", "arguments": {"center": {"lat": "候选点1"}, "radius": 500, "dataset": "pois"}},
            {"tool": "spatial_buffer", "arguments": {"center": {"lat": "候选点2"}, "radius": 500, "dataset": "pois"}},
            {"tool": "spatial_buffer", "arguments": {"center": {"lat": "候选点3"}, "radius": 500, "dataset": "pois"}},
        ],
        "note": "三次独立调用后在 SUMMARIZING 阶段比较 count / 类别分布，给出有依据的排序。",
    },
]


def load_full_spec() -> SkillFullSpec:
    return SkillFullSpec(name="spatial_analysis", usage_notes=USAGE_NOTES, examples=EXAMPLES)
