"""Reasoning engines.

The orchestration logic — state, gates, validation, git — is deterministic
Python. Only the genuinely open-ended steps (turning a ticket into
requirements, a plan, code and a review) are delegated to an engine, behind a
narrow interface with a schema contract. Swapping Claude Code for the Agent SDK
(or a stub in tests) changes nothing else in the system.
"""

from orchestrator.engine.base import Engine, EngineMode, EngineRequest, EngineResponse
from orchestrator.engine.factory import build_engine

__all__ = ["Engine", "EngineMode", "EngineRequest", "EngineResponse", "build_engine"]
