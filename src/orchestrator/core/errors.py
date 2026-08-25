"""Typed error hierarchy.

Every failure the orchestrator raises on purpose derives from ``OrchestratorError``
so the CLI can render an actionable message instead of a traceback, and so the
workflow can decide whether a failure is recoverable (resume) or terminal.
"""

from __future__ import annotations


class OrchestratorError(Exception):
    """Base class for all orchestrator errors."""

    exit_code: int = 1
    #: Whether a run that failed with this error can be resumed after the user
    #: fixes the underlying condition.
    recoverable: bool = True

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.hint:
            return f"{self.message}\nHint: {self.hint}"
        return self.message


class ConfigurationError(OrchestratorError):
    """Missing or invalid configuration."""

    exit_code = 78  # EX_CONFIG


class CredentialError(ConfigurationError):
    """A required credential is missing or was rejected by the remote service."""


class RepositoryError(OrchestratorError):
    """The target repository is missing, not a git repo, or otherwise unusable."""

    exit_code = 66  # EX_NOINPUT


class PlatformDetectionError(RepositoryError):
    """The target repository could not be classified."""


class IssueTrackerError(OrchestratorError):
    """The issue tracker could not satisfy the request."""

    exit_code = 69  # EX_UNAVAILABLE


class IssueNotFoundError(IssueTrackerError):
    """The requested issue does not exist or is not visible to these credentials."""

    recoverable = False


class EngineError(OrchestratorError):
    """The reasoning engine failed or produced unusable output."""

    exit_code = 70  # EX_SOFTWARE


class EngineUnavailableError(EngineError):
    """The configured engine backend is not installed or not reachable."""


class StructuredOutputError(EngineError):
    """The engine returned output that does not satisfy the expected schema."""


class StateError(OrchestratorError):
    """An illegal workflow transition or a corrupt state file."""


class ApprovalRequired(OrchestratorError):
    """Raised to stop a non-interactive run at a human approval gate."""

    exit_code = 75  # EX_TEMPFAIL

    def __init__(self, gate: str, run_id: str) -> None:
        super().__init__(
            f"Approval required at gate '{gate}'.",
            hint=f"Review with `orc status {run_id}`, then `orc approve {run_id}`.",
        )
        self.gate = gate
        self.run_id = run_id


class ApprovalRejected(OrchestratorError):
    """The developer rejected a gate; the run stops cleanly."""

    exit_code = 0
    recoverable = False


class ValidationFailed(OrchestratorError):
    """Deterministic validation did not pass."""

    exit_code = 65  # EX_DATAERR
