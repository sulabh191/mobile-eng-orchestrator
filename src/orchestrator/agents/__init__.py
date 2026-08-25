"""Agents.

Each agent owns exactly one responsibility and one output type. None of them
knows about the others: the workflow wires them together. That is what keeps a
new phase (design review, release notes, localisation) a matter of adding a
module rather than editing a monolith.
"""

from orchestrator.agents.base import Agent, AgentContext
from orchestrator.agents.registry import AGENT_REGISTRY, describe_agents, get_agent

__all__ = ["AGENT_REGISTRY", "Agent", "AgentContext", "describe_agents", "get_agent"]
