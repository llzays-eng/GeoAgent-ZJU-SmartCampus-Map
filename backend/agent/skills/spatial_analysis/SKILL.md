---
name: spatial_analysis
description: 缓冲区分析与多点位综合统计，例如"某范围内有多少间安静自习室""这三个候选点周边条件对比"
tools: spatial_buffer
---

# Spatial Analysis Skill

## 何时选用

- 问题需要统计"某范围内全部符合条件的点位"，而不是"最近的N个"
  （区别于 poi_search 技能的 search_poi）
- 需要对多个候选地点做横向对比（每个候选点各自做一次 buffer 查询，再比较统计结果）

## 参数细节 / 示例

见 `impl.py::load_full_spec()`。
