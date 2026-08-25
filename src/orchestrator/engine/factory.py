"""Engine selection."""

from __future__ import annotations

from orchestrator.core.config import Settings
from orchestrator.core.errors import ConfigurationError
from orchestrator.engine.base import Engine

_BACKENDS = ("claude_code", "agent_sdk", "mock")


def build_engine(settings: Settings, *, backend: str | None = None) -> Engine:
    name = (backend or settings.engine.backend or "claude_code").lower()
    if name == "mock":
        from orchestrator.engine.mock import MockEngine

        return MockEngine()
    if name == "claude_code":
        from orchestrator.engine.claude_code import ClaudeCodeEngine

        return ClaudeCodeEngine(settings.engine)
    if name == "agent_sdk":
        from orchestrator.engine.agent_sdk import AgentSDKEngine

        return AgentSDKEngine(settings.engine)
    raise ConfigurationError(
        f"Unknown engine backend '{name}'.", hint=f"Choose one of: {', '.join(_BACKENDS)}"
    )


def known_backends() -> tuple[str, ...]:
    return _BACKENDS
