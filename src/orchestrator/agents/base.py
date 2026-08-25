"""The agent contract."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from orchestrator.core.config import Settings
from orchestrator.core.logging import get_logger
from orchestrator.core.models import AuditEvent
from orchestrator.core.state import StateStore, WorkflowState
from orchestrator.engine.base import Engine
from orchestrator.inspection.profile import RepoProfile
from orchestrator.integrations.jira.protocol import JiraClientProtocol


@dataclass
class AgentContext:
    """Everything an agent is allowed to touch.

    Agents receive this and nothing else — no globals, no ambient config — which
    is what makes them unit-testable and safe to run in any order the workflow
    decides.
    """

    settings: Settings
    store: StateStore
    state: WorkflowState
    profile: RepoProfile
    engine: Engine
    tracker: JiraClientProtocol | None = None
    #: Scratch space shared between agents within a single run.
    blackboard: dict[str, Any] = field(default_factory=dict)

    @property
    def repo_path(self) -> Path:
        return self.profile.path

    @property
    def issue_key(self) -> str:
        return self.state.issue_key

    def log(self, agent: str) -> logging.Logger:
        return get_logger(f"agent.{agent}")

    def audit(self, actor: str, event: str, **detail: Any) -> None:
        self.store.append_audit(
            AuditEvent(run_id=self.state.run_id, actor=actor, event=event, detail=detail)
        )

    def save_artifact(self, name: str, content: str | dict[str, Any]) -> Path:
        path = self.store.write_artifact(self.state, name, content)
        self.store.save(self.state)
        return path


class Agent(ABC):
    """Base class. One agent, one job, one output model."""

    #: Stable identifier used in state, audit events, the CLI and generated
    #: Claude Code subagent definitions.
    name: str = "agent"
    #: One sentence, written for a human reading `orc agents`.
    responsibility: str = ""
    #: The model this agent returns.
    output_model: type[BaseModel] | None = None
    #: Whether this agent may modify the target repository.
    mutates_repository: bool = False

    @abstractmethod
    def run(self, ctx: AgentContext, **kwargs: Any) -> BaseModel:
        """Do the one thing this agent is for."""

    # -- shared helpers ------------------------------------------------------- #

    def artifact_name(self, extension: str = "json") -> str:
        return f"{self.name}.{extension}"

    def emit(self, ctx: AgentContext, payload: BaseModel) -> Path:
        """Persist this agent's output as a run artifact."""
        return ctx.save_artifact(
            self.artifact_name(), payload.model_dump(mode="json")
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r}>"
