"""agent/subagents/data_retrieval.py — outline section 6.

Handles "find/locate" style goals. Permanently constrained to the three
tools that only look things up (never mutates anything, never runs spatial
statistics) — that boundary is what keeps this Subagent's role legible.
"""
from __future__ import annotations

from agent.subagents.base import BaseSubagent


class DataRetrievalSubagent(BaseSubagent):
    name = "data_retrieval"
    allowed_tools = ("search_poi", "geocode", "query_charging_pile")
