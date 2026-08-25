"""Reusable skills: named blocks of engineering guidance injected into prompts.

A skill is a markdown file with YAML frontmatter. Teams extend the orchestrator
by dropping a file into ``~/.config/engineering-orchestrator/skills/`` or into
``<repo>/.orchestrator/skills/`` — no code change required.
"""

from orchestrator.skills.loader import Skill, SkillLibrary, load_skills

__all__ = ["Skill", "SkillLibrary", "load_skills"]
