"""Offline Jira client backed by JSON fixtures.

Used by the test suite, by `orc run --engine mock`, and by anyone who wants to
walk the whole workflow on a plane. It implements the same protocol and the same
error semantics as the real client, including "issue not found".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.core.errors import IssueNotFoundError
from orchestrator.core.models import TrackerIssue
from orchestrator.integrations.jira.adf import extract_bullets

DEFAULT_FIXTURES: dict[str, dict[str, Any]] = {
    "MOB-101": {
        "key": "MOB-101",
        "summary": "Add pull-to-refresh to the order history screen",
        "description": (
            "Users cannot refresh their order history without leaving and re-entering "
            "the screen.\n\n"
            "Add a pull-to-refresh gesture that re-fetches the order list and shows a "
            "spinner while loading. Preserve scroll position where possible and surface "
            "a non-blocking error state when the refresh fails.\n\n"
            "Acceptance criteria:\n"
            "- Pulling down on the order history list triggers a refresh\n"
            "- A loading indicator is visible while the refresh is in flight\n"
            "- A failed refresh shows an inline retry affordance, not a modal\n"
            "- Analytics event `order_history_refreshed` is emitted on success\n"
        ),
        "issue_type": "Story",
        "status": "Ready for Development",
        "priority": "Medium",
        "labels": ["mobile", "orders"],
        "components": ["Orders"],
        "assignee": "Sam Developer",
        "reporter": "Priya PM",
        "url": "https://example.atlassian.net/browse/MOB-101",
    },
    "MOB-102": {
        "key": "MOB-102",
        "summary": "Crash on cold start when cached session token is expired",
        "description": (
            "A cold start with an expired cached token crashes in the session bootstrap "
            "path instead of routing the user to sign-in.\n\n"
            "Acceptance criteria:\n"
            "- Expired cached token routes to the sign-in screen\n"
            "- No crash is reported in the bootstrap path\n"
            "- A regression test covers the expired-token cold start\n"
        ),
        "issue_type": "Bug",
        "status": "Ready for Development",
        "priority": "High",
        "labels": ["mobile", "crash"],
        "components": ["Auth"],
        "url": "https://example.atlassian.net/browse/MOB-102",
    },
}


class MockJiraClient:
    """In-memory tracker used for tests and offline development."""

    name = "mock"

    def __init__(
        self,
        issues: dict[str, dict[str, Any]] | None = None,
        *,
        fixtures_dir: Path | None = None,
    ) -> None:
        self._issues: dict[str, TrackerIssue] = {}
        for key, payload in (issues or DEFAULT_FIXTURES).items():
            self._issues[key.upper()] = self._normalise(TrackerIssue.model_validate(payload))
        if fixtures_dir:
            self.load_fixtures(fixtures_dir)
        #: Everything written through this client, so tests can assert on it.
        self.comments: list[tuple[str, str]] = []
        self.transitions: list[tuple[str, str]] = []

    # -- fixtures ------------------------------------------------------------- #

    def load_fixtures(self, directory: Path) -> None:
        for path in sorted(Path(directory).glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for item in payload if isinstance(payload, list) else [payload]:
                issue = self._normalise(TrackerIssue.model_validate(item))
                self._issues[issue.key.upper()] = issue

    def add_issue(self, issue: TrackerIssue) -> None:
        self._issues[issue.key.upper()] = self._normalise(issue)

    @staticmethod
    def _normalise(issue: TrackerIssue) -> TrackerIssue:
        """Mirror the real client: lift acceptance criteria out of the description."""
        if issue.acceptance_criteria:
            return issue
        lowered = issue.description.lower()
        index = lowered.find("acceptance criteria")
        if index != -1:
            issue.acceptance_criteria = extract_bullets(issue.description[index:])
        return issue

    # -- protocol surface ------------------------------------------------------ #

    def get_issue(self, key: str) -> TrackerIssue:
        issue = self._issues.get(key.strip().upper())
        if issue is None:
            raise IssueNotFoundError(
                f"Mock tracker has no issue '{key}'.",
                hint=f"Known keys: {', '.join(sorted(self._issues)) or 'none'}",
            )
        return issue.model_copy(deep=True)

    def search(self, jql: str, *, limit: int = 25) -> list[TrackerIssue]:
        needle = jql.lower()
        matches = [
            issue
            for issue in self._issues.values()
            if needle in issue.summary.lower()
            or needle in issue.key.lower()
            or needle in issue.status.lower()
            or "order by" in needle
        ]
        return [m.model_copy(deep=True) for m in matches[:limit]]

    def add_comment(self, key: str, body: str) -> None:
        self.get_issue(key)  # raises if unknown
        self.comments.append((key.upper(), body))

    def available_transitions(self, key: str) -> dict[str, str]:
        self.get_issue(key)
        return {"In Progress": "21", "In Review": "31", "Done": "41"}

    def transition(self, key: str, transition: str) -> None:
        available = self.available_transitions(key)
        if transition not in available and transition not in available.values():
            from orchestrator.core.errors import IssueTrackerError

            raise IssueTrackerError(f"Transition '{transition}' unavailable for {key}.")
        issue = self._issues[key.upper()]
        for name, ident in available.items():
            if transition in (name, ident):
                issue.status = name
        self.transitions.append((key.upper(), transition))

    def health_check(self) -> dict[str, str]:
        return {
            "backend": self.name,
            "base_url": "(offline fixtures)",
            "issues": str(len(self._issues)),
        }
