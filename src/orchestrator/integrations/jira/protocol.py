"""The tracker contract every client implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from orchestrator.core.models import TrackerIssue


@runtime_checkable
class JiraClientProtocol(Protocol):
    """Minimum surface the orchestrator needs from an issue tracker.

    Implementations must map their native payloads onto :class:`TrackerIssue`
    and must raise :class:`orchestrator.core.errors.IssueTrackerError`
    subclasses for every failure — callers never see transport exceptions.
    """

    #: Human-readable name used in logs and `orc doctor` output.
    name: str

    def get_issue(self, key: str) -> TrackerIssue:
        """Fetch a single issue, including acceptance criteria and comments."""
        ...

    def search(self, jql: str, *, limit: int = 25) -> list[TrackerIssue]:
        """Run a JQL (or backend-equivalent) query."""
        ...

    def add_comment(self, key: str, body: str) -> None:
        """Post a comment. Used to link the PR back to the ticket."""
        ...

    def available_transitions(self, key: str) -> dict[str, str]:
        """Map of transition name -> transition id for the issue's current state."""
        ...

    def transition(self, key: str, transition: str) -> None:
        """Move the issue to a new status by transition name or id."""
        ...

    def health_check(self) -> dict[str, str]:
        """Cheap connectivity/auth probe for `orc doctor`."""
        ...
