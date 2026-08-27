"""agent/subagents/reporting.py — outline section 6.

Handles visualization/presentation goals. Deliberately the smallest
Subagent (one tool) — chart generation doesn't need its own reasoning about
*what* to chart, just how, so this is mostly a thin wrapper. Kept as its
own Subagent (rather than folding generate_chart into spatial_analysis)
because "turn a result into a chart" is a distinct step in the task graph
with its own dependency edge (it always depends_on the task that produced
the data), which reads more clearly as a separate node.
"""
from __future__ import annotations

from agent.subagents.base import BaseSubagent


class ReportingSubagent(BaseSubagent):
    name = "reporting"
    allowed_tools = ("generate_chart",)
