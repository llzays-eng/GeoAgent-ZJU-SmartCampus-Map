---
name: poi_search
description: 查找校园内的自习室、POI（图书馆/食堂/教学楼等）或充电桩，支持按地名/坐标定位与最近邻检索
tools: search_poi, geocode, query_charging_pile
---

# POI Search Skill

仅当 Orchestrator 在任务规划阶段判断当前问题涉及"查找/定位某类地点"时，
才会加载本文件的完整内容（本节以下部分）进入对应 Subagent 的上下文；
Tier-1 阶段只暴露上面 frontmatter 里的一句话描述。

## 何时选用

- 用户想找某类地点（自习室/图书馆/食堂/充电桩等）
- 用户提到的是地名而非坐标（需要先 geocode）
- 问题包含"离我最近""附近有没有""哪里可以"等定位型意图

## 工具组合建议

1. 若用户给的是地名 → 先 `geocode(address)` 得到坐标
2. 用得到的坐标调用 `search_poi(center, category, radius)`
3. 若问题涉及充电桩 → `query_charging_pile(location)`

## 参数细节 / 示例（完整版，仅被选中时加载）

见 `impl.py::load_full_spec()`，包含每个工具的字段级说明与 2-3 个真实调用示例。
