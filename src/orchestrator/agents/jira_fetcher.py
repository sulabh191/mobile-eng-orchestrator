"""Jira Fetcher agent — the only agent that talks to the issue tracker.

Deterministic by design: no model is involved in reading a ticket. Its output is
a :class:`TrackerIssue`, which every downstream agent treats as the single
source of truth for what was asked.
"""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import Agent, AgentContext
from orchestrator.core.errors import IssueTrackerError
from orchestrator.core.models import TrackerIssue


class JiraFetcherAgent(Agent):
    name = "jira-fetcher"
    responsibility = "Fetch the ticket and normalise it into a tracker-neutral issue."
    output_model = TrackerIssue

    def run(self, ctx: AgentContext, **kwargs: Any) -> TrackerIssue:
        log = ctx.log(self.name)
        if ctx.tracker is None:
            raise IssueTrackerError(
                "No issue tracker client is configured for this run.",
                hint="Run with --offline to use fixtures, or configure Jira credentials.",
            )

        key = str(kwargs.get("issue_key") or ctx.issue_key)
        log.info("fetching %s from %s", key, ctx.tracker.name)
        issue = ctx.tracker.get_issue(key)

        if not issue.summary.strip():
            raise IssueTrackerError(f"{key} has no summary; refusing to work from an empty ticket.")

        ctx.audit(
            self.name,
            "issue.fetched",
            issue_key=issue.key,
            status=issue.status,
            issue_type=issue.issue_type,
            acceptance_criteria=len(issue.acceptance_criteria),
            comments=len(issue.comments),
            tracker=ctx.tracker.name,
        )
        self.emit(ctx, issue)
        ctx.save_artifact("issue.md", issue.as_prompt_block())
        ctx.blackboard["issue"] = issue
        return issue

    def comment_on_issue(self, ctx: AgentContext, body: str) -> bool:
        """Post a comment (used to link the PR back). Never fatal."""
        if ctx.tracker is None:
            return False
        try:
            ctx.tracker.add_comment(ctx.issue_key, body)
        except IssueTrackerError as exc:  # pragma: no cover - network dependent
            ctx.log(self.name).warning("could not comment on %s: %s", ctx.issue_key, exc)
            return False
        ctx.audit(self.name, "issue.commented", issue_key=ctx.issue_key)
        return True
