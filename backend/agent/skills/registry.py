"""agent/skills/registry.py — Skills system (outline section 7).

The problem this solves: with 6 tools today and a plan to grow, stuffing
every tool's full parameter docs + examples into the system prompt on every
single call doesn't scale — that's "把六个工具的完整说明一次性全塞进
system prompt", which the outline calls out as the lazy default most demo
projects ship. Progressive disclosure fixes it with two tiers:

  Tier 1 (always resident, cheap): each skills/<name>/SKILL.md front-matter
      `description:` line — a few dozen characters, shown to the LLM during
      TASK_PLANNING so it can decide which capability *domains* are even
      relevant to this query.
  Tier 2 (loaded only on demand): skills/<name>/impl.py's
      `load_full_spec()` — the tool JSON schemas + usage examples for that
      domain — only pulled into context for skills the planning step
      actually selected.

Skills vs. Subagents — these are different axes, not duplicates:
  * a Skill is a *capability domain* used to decide relevance before any
    tool schema is even loaded (an Orchestrator-level, planning-time
    concept — closer to "what is this question about").
  * a Subagent is a *role* with a fixed, permanently-constrained tool set
    that actually executes calls (outline section 6 — closer to "who does
    the work"). One Subagent (spatial_analysis) executes tools from two
    different Skill domains (spatial_analysis + ndvi_analysis) because
    those are genuinely different knowledge domains that happen to share an
    execution role.
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class SkillSummary:
    """Tier-1: what the Orchestrator always sees."""

    name: str
    description: str
    tools: list[str]


@dataclass
class SkillFullSpec:
    """Tier-2: only loaded for skills selected during planning."""

    name: str
    usage_notes: str
    examples: list[dict]


def _parse_frontmatter(text: str) -> dict[str, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


class SkillRegistry:
    def __init__(self) -> None:
        self._summaries: dict[str, SkillSummary] = {}
        self._discover()

    def _discover(self) -> None:
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_dir.is_dir() or not skill_md.exists():
                continue
            fields = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            name = fields.get("name", skill_dir.name)
            description = fields.get("description", "")
            tools = [t.strip() for t in fields.get("tools", "").split(",") if t.strip()]
            self._summaries[name] = SkillSummary(name=name, description=description, tools=tools)

    def list_summaries(self) -> list[SkillSummary]:
        """Tier-1 menu — cheap, always shown to the LLM at planning time."""
        return list(self._summaries.values())

    def prompt_menu(self) -> str:
        """Render the tier-1 menu as compact text for the planning prompt."""
        lines = []
        for s in self._summaries.values():
            lines.append(f"- {s.name}: {s.description}")
        return "\n".join(lines)

    def tools_for(self, skill_names: list[str]) -> list[str]:
        tools: list[str] = []
        for name in skill_names:
            summary = self._summaries.get(name)
            if summary:
                tools.extend(t for t in summary.tools if t not in tools)
        return tools

    def load_full_spec(self, skill_name: str) -> SkillFullSpec | None:
        """Tier-2 — only call this for skills selected during planning."""
        if skill_name not in self._summaries:
            return None
        module = importlib.import_module(f"agent.skills.{skill_name}.impl")
        return module.load_full_spec()

    def all_names(self) -> list[str]:
        return list(self._summaries.keys())


_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry
