"""Discovery and rendering of skills."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from orchestrator.core.logging import get_logger
from orchestrator.core.models import Platform

logger = get_logger("skills")

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

BUILTIN_DIR = Path(__file__).parent / "library"


@dataclass
class Skill:
    name: str
    description: str
    body: str
    applies_to: list[Platform] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: str = "builtin"

    def matches(self, platform: Platform) -> bool:
        return not self.applies_to or platform in self.applies_to

    def render(self) -> str:
        return f"### Skill: {self.name}\n\n{self.body.strip()}\n"


def _parse(path: Path, source: str) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover
        logger.warning("could not read skill %s: %s", path, exc)
        return None

    meta: dict = {}
    body = text
    if match := FRONTMATTER.match(text):
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            logger.warning("invalid frontmatter in %s: %s", path, exc)
            meta = {}
        body = text[match.end() :]

    platforms: list[Platform] = []
    for value in meta.get("applies_to", []) or []:
        try:
            platforms.append(Platform(str(value).lower()))
        except ValueError:
            logger.warning("unknown platform %r in %s", value, path)

    return Skill(
        name=str(meta.get("name", path.stem)),
        description=str(meta.get("description", "")),
        body=body,
        applies_to=platforms,
        tags=[str(t) for t in (meta.get("tags") or [])],
        source=source,
    )


class SkillLibrary:
    """All skills visible to this run, latest source winning on name collision."""

    def __init__(self, skills: list[Skill] | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        for skill in skills or []:
            self.add(skill)

    def add(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def __len__(self) -> int:
        return len(self._skills)

    def __iter__(self):
        return iter(self._skills.values())

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def for_platform(self, platform: Platform) -> list[Skill]:
        return [s for s in self._skills.values() if s.matches(platform)]

    def for_tags(self, *tags: str, platform: Platform | None = None) -> list[Skill]:
        wanted = {t.lower() for t in tags}
        return [
            skill
            for skill in self._skills.values()
            if wanted & {t.lower() for t in skill.tags}
            and (platform is None or skill.matches(platform))
        ]

    def render(self, *tags: str, platform: Platform | None = None) -> str:
        selected = self.for_tags(*tags, platform=platform) if tags else self.for_platform(
            platform or Platform.GENERIC
        )
        if not selected:
            return ""
        blocks = [skill.render() for skill in sorted(selected, key=lambda s: s.name)]
        return "## Applicable engineering skills\n\n" + "\n".join(blocks)


def load_skills(
    *, user_dir: Path | None = None, repo_dir: Path | None = None
) -> SkillLibrary:
    """Load builtin skills, then user overrides, then repository overrides."""
    library = SkillLibrary()
    for directory, source in (
        (BUILTIN_DIR, "builtin"),
        (user_dir, "user"),
        (repo_dir, "repository"),
    ):
        if directory is None or not Path(directory).is_dir():
            continue
        for path in sorted(Path(directory).glob("*.md")):
            skill = _parse(path, source)
            if skill:
                library.add(skill)
    return library
