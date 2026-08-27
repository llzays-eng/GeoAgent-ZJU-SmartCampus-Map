---
name: ndvi_analysis
description: 查询某区域多年 NDVI 植被指数趋势（遥感/生态分析类问题）
tools: ndvi_trend
---

# NDVI Analysis Skill

## 何时选用

- 问题涉及"植被恢复情况""绿化变化""生态指标趋势"等，且需要具体年份序列
- 不要用于"NDVI是什么意思"这类定义性问题——那类问题应该走 RAG 检索
  （agent/rag），而不是调用这个工具；工具返回数字，RAG 返回解释。

## 参数细节 / 示例

见 `impl.py::load_full_spec()`。**必须**在最终回答中原样保留工具返回的
`disclaimer` 字段（当 data_source=synthetic_demo 时），不能省略或弱化。
