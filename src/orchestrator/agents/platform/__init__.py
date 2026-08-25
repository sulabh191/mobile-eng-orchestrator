"""Platform specialists.

These agents do not run a phase of their own. They advise the pipeline: they
contribute platform guidance to prompts and run cheap pre-flight checks before
any expensive work starts. Supporting a new platform means adding one module
here plus a validation module — nothing in the workflow changes.
"""

from orchestrator.agents.platform.base import PlatformAgent, get_platform_agent

__all__ = ["PlatformAgent", "get_platform_agent"]
