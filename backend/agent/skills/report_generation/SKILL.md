---
name: report_generation
description: 把数据结果转成图表（柱状图/折线图/饼图），用于可视化呈现分析结论
tools: generate_chart
---

# Report Generation Skill

## 何时选用

- 用户明确要求"做个图表""可视化一下"
- 汇总阶段判断数字结果（NDVI序列、缓冲区统计分布等）用图表比纯文字更清楚

## 参数细节 / 示例

见 `impl.py::load_full_spec()`。
