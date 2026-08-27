"""agent/subagents/registry.py — name -> Subagent class, used by the
Orchestrator to dispatch a TaskSpec (which only carries a subagent *name*,
per agent/schemas.py) to the right constrained executor."""
from __future__ import annotations

from agent.subagents.base import BaseSubagent
from agent.subagents.data_retrieval import DataRetrievalSubagent
from agent.subagents.reporting import ReportingSubagent
from agent.subagents.spatial_analysis import SpatialAnalysisSubagent

SUBAGENT_CLASSES: dict[str, type[BaseSubagent]] = {
    "data_retrieval": DataRetrievalSubagent,
    "spatial_analysis": SpatialAnalysisSubagent,
    "reporting": ReportingSubagent,
}
