"""Prompt construction shared by the reasoning agents.

Prompts are assembled from four stable parts — role, repository context,
applicable skills, and the task payload — so that a change to conventions is a
change to a skill file, not to Python.
"""

from __future__ import annotations

from orchestrator.core.models import Platform
from orchestrator.inspection.profile import RepoProfile
from orchestrator.skills.loader import SkillLibrary

BASE_ROLE = (
    "You are one specialised agent inside a larger engineering orchestration system. "
    "You have exactly one job, described below. Do that job and nothing else: another "
    "agent handles every other phase, a human approves each gate, and deterministic "
    "checks — not your judgement — decide whether the work passes."
)

HONESTY_CLAUSE = (
    "Report uncertainty explicitly rather than smoothing it over. If the repository "
    "contradicts the ticket, say so. A flagged gap is cheap; a confident wrong answer is "
    "expensive."
)


def system_prompt(role: str) -> str:
    return f"{BASE_ROLE}\n\nYour job: {role}\n\n{HONESTY_CLAUSE}"


def repository_block(profile: RepoProfile) -> str:
    return "## Repository context\n\n" + profile.as_prompt_block()


def skills_block(library: SkillLibrary, *tags: str, platform: Platform) -> str:
    return library.render(*tags, platform=platform)


def compose(*blocks: str) -> str:
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())
