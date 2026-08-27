"""agent/subagents/spatial_analysis.py — outline section 6.

Handles spatial-statistics and remote-sensing goals. Note this Subagent's
tool set spans two different Skill domains (spatial_analysis + ndvi_analysis,
see agent/skills/) — that's intentional: Skills group tools by *knowledge
domain* for planning-time relevance filtering, Subagents group tools by
*execution role*. One role can legitimately span two knowledge domains.
"""
from __future__ import annotations

from agent.subagents.base import BaseSubagent


class SpatialAnalysisSubagent(BaseSubagent):
    name = "spatial_analysis"
    allowed_tools = ("spatial_buffer", "ndvi_trend")
