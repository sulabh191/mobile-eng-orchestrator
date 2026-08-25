"""The agent registry.

One place that knows every agent, what it is responsible for, what it produces
and whether it can modify the repository. The CLI (`orc agents`), the Claude
Code asset generator and the docs all read from here, so an agent added to this
dict becomes discoverable everywhere at once.
"""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.agents.base import Agent
from orchestrator.agents.git_pr import DeliveryAgent
from orchestrator.agents.implementer import ImplementationAgent
from orchestrator.agents.jira_fetcher import JiraFetcherAgent
from orchestrator.agents.planner import PlanAgent
from orchestrator.agents.platform.android import AndroidPlatformAgent
from orchestrator.agents.platform.ios import IOSPlatformAgent
from orchestrator.agents.requirements import RequirementsAgent
from orchestrator.agents.reviewer import ReviewAgent
from orchestrator.agents.validator import ValidationAgent
from orchestrator.core.errors import ConfigurationError


@dataclass(frozen=True)
class AgentSpec:
    factory: type[Agent]
    phase: str
    consumes: str
    produces: str
    uses_engine: bool

    def instantiate(self) -> Agent:
        return self.factory()


AGENT_REGISTRY: dict[str, AgentSpec] = {
    "jira-fetcher": AgentSpec(JiraFetcherAgent, "fetch", "issue key", "TrackerIssue", False),
    "requirements": AgentSpec(RequirementsAgent, "requirements", "TrackerIssue", "RequirementsDoc", True),
    "planner": AgentSpec(PlanAgent, "plan", "RequirementsDoc", "ImplementationPlan", True),
    "implementer": AgentSpec(
        ImplementationAgent, "implement", "ImplementationPlan", "ImplementationResult", True
    ),
    "validator": AgentSpec(
        ValidationAgent, "validate", "ImplementationResult", "ValidationReport", False
    ),
    "reviewer": AgentSpec(ReviewAgent, "review", "ValidationReport", "ReviewSummary", True),
    "delivery": AgentSpec(DeliveryAgent, "deliver", "ReviewSummary", "DeliveryResult", False),
    "platform-ios": AgentSpec(IOSPlatformAgent, "advise", "RepoProfile", "PreflightReport", False),
    "platform-android": AgentSpec(
        AndroidPlatformAgent, "advise", "RepoProfile", "PreflightReport", False
    ),
}

#: The pipeline order, used by the workflow and by the generated documentation.
PIPELINE: tuple[str, ...] = (
    "jira-fetcher",
    "requirements",
    "planner",
    "implementer",
    "validator",
    "reviewer",
    "delivery",
)


def get_agent(name: str) -> Agent:
    spec = AGENT_REGISTRY.get(name)
    if spec is None:
        raise ConfigurationError(
            f"Unknown agent '{name}'.", hint=f"Known agents: {', '.join(sorted(AGENT_REGISTRY))}"
        )
    return spec.instantiate()


def describe_agents() -> list[dict[str, str]]:
    rows = []
    for name, spec in AGENT_REGISTRY.items():
        agent = spec.instantiate()
        rows.append(
            {
                "name": name,
                "phase": spec.phase,
                "responsibility": agent.responsibility,
                "consumes": spec.consumes,
                "produces": spec.produces,
                "engine": "yes" if spec.uses_engine else "no",
                "writes": "yes" if agent.mutates_repository else "no",
            }
        )
    return rows
