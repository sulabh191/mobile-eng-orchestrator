"""Platform agent contract."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import Agent, AgentContext
from orchestrator.core.models import Platform, StrictModel


class PreflightReport(StrictModel):
    platform: Platform
    warnings: list[str] = []
    blockers: list[str] = []
    facts: dict[str, str] = {}

    @property
    def ok(self) -> bool:
        return not self.blockers


class PlatformAgent(Agent):
    """Advises the pipeline about one target platform."""

    platform: Platform = Platform.GENERIC

    #: Skill tags this platform contributes to every prompt.
    skill_tags: tuple[str, ...] = ()

    def run(self, ctx: AgentContext, **kwargs: Any) -> PreflightReport:
        return self.preflight(ctx)

    def preflight(self, ctx: AgentContext) -> PreflightReport:
        """Cheap, read-only sanity checks run before the first expensive phase."""
        return PreflightReport(platform=self.platform)

    def guidance(self, ctx: AgentContext) -> str:
        """Extra prompt text beyond the skill library, if any."""
        return ""


class GenericPlatformAgent(PlatformAgent):
    name = "platform-generic"
    responsibility = "Fallback specialist for repositories that are neither iOS nor Android."
    platform = Platform.GENERIC

    def preflight(self, ctx: AgentContext) -> PreflightReport:
        report = PreflightReport(platform=Platform.GENERIC)
        report.warnings.append(
            "Platform not recognised as iOS or Android; only generic guard checks will run."
        )
        return report


def get_platform_agent(platform: Platform) -> PlatformAgent:
    from orchestrator.agents.platform.android import AndroidPlatformAgent
    from orchestrator.agents.platform.ios import IOSPlatformAgent

    return {
        Platform.IOS: IOSPlatformAgent(),
        Platform.ANDROID: AndroidPlatformAgent(),
    }.get(platform, GenericPlatformAgent())
