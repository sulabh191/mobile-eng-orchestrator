"""Structured data contracts shared by every agent.

Agents never pass free-form prose to one another. Each agent consumes a typed
model and emits a typed model, which is what makes the pipeline auditable,
resumable and testable. All models are JSON round-trippable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    """Timezone-aware UTC now (naive datetimes are banned in persisted state)."""
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    """Base model: ignores unknown keys on input, serialises deterministically."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, ser_json_timedelta="float")


# --------------------------------------------------------------------------- #
# Platform
# --------------------------------------------------------------------------- #


class Platform(str, Enum):
    IOS = "ios"
    ANDROID = "android"
    GENERIC = "generic"

    @property
    def display(self) -> str:
        return {"ios": "iOS", "android": "Android", "generic": "Generic"}[self.value]


# --------------------------------------------------------------------------- #
# Issue tracker (tracker-neutral: the Jira layer maps its payloads onto these)
# --------------------------------------------------------------------------- #


class IssueAttachment(StrictModel):
    filename: str
    url: str
    mime_type: str | None = None
    size_bytes: int | None = None


class IssueComment(StrictModel):
    author: str | None = None
    created: datetime | None = None
    body: str = ""


class TrackerIssue(StrictModel):
    """A tracker-agnostic issue. Jira-specific shapes never leave the Jira layer."""

    key: str
    summary: str
    description: str = ""
    issue_type: str = "Task"
    status: str = "Unknown"
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    assignee: str | None = None
    reporter: str | None = None
    parent_key: str | None = None
    subtask_keys: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    attachments: list[IssueAttachment] = Field(default_factory=list)
    comments: list[IssueComment] = Field(default_factory=list)
    url: str | None = None

    def as_prompt_block(self) -> str:
        """Render the issue as a stable text block for engine prompts."""
        lines = [
            f"Issue: {self.key} — {self.summary}",
            f"Type: {self.issue_type} | Status: {self.status} | Priority: {self.priority or 'n/a'}",
        ]
        if self.labels:
            lines.append(f"Labels: {', '.join(self.labels)}")
        if self.components:
            lines.append(f"Components: {', '.join(self.components)}")
        lines.append("")
        lines.append("Description:")
        lines.append(self.description.strip() or "(empty)")
        if self.acceptance_criteria:
            lines.append("")
            lines.append("Acceptance criteria stated on the ticket:")
            lines.extend(f"- {ac}" for ac in self.acceptance_criteria)
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Requirements
# --------------------------------------------------------------------------- #


class Requirement(StrictModel):
    id: str = Field(description="Stable short id, e.g. R1")
    title: str
    statement: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: Literal["must", "should", "could"] = "must"
    source: str = Field(default="ticket", description="ticket | comment | inferred")


class RequirementsDoc(StrictModel):
    issue_key: str
    platform: Platform
    summary: str
    requirements: list[Requirement] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    @property
    def blocking_questions(self) -> list[str]:
        return self.open_questions


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #


class PlanStep(StrictModel):
    id: str = Field(description="Stable short id, e.g. S1")
    title: str
    intent: str = Field(description="What this step accomplishes and why")
    target_files: list[str] = Field(default_factory=list)
    satisfies: list[str] = Field(default_factory=list, description="Requirement ids")
    depends_on: list[str] = Field(default_factory=list, description="PlanStep ids")
    verification: str = Field(default="", description="How to tell this step worked")
    risk: Literal["low", "medium", "high"] = "low"


class ImplementationPlan(StrictModel):
    issue_key: str
    platform: Platform
    summary: str
    steps: list[PlanStep] = Field(default_factory=list)
    test_strategy: list[str] = Field(default_factory=list)
    rollback: str = ""
    risks: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)

    def ordered_steps(self) -> list[PlanStep]:
        """Topologically order steps by ``depends_on``; stable for equal ranks."""
        remaining = {s.id: s for s in self.steps}
        emitted: list[PlanStep] = []
        seen: set[str] = set()
        while remaining:
            ready = [
                s
                for s in remaining.values()
                if all(dep in seen or dep not in remaining for dep in s.depends_on)
            ]
            if not ready:  # dependency cycle — fall back to declaration order
                ready = list(remaining.values())
            for step in ready:
                emitted.append(step)
                seen.add(step.id)
                remaining.pop(step.id, None)
        return emitted


# --------------------------------------------------------------------------- #
# Implementation
# --------------------------------------------------------------------------- #


class FileChange(StrictModel):
    path: str
    change_type: Literal["added", "modified", "deleted", "renamed"] = "modified"
    lines_added: int = 0
    lines_removed: int = 0


class ImplementationResult(StrictModel):
    issue_key: str
    completed_step_ids: list[str] = Field(default_factory=list)
    skipped_step_ids: list[str] = Field(default_factory=list)
    file_changes: list[FileChange] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    transcript_path: str | None = None

    @property
    def touched_paths(self) -> list[str]:
        return [fc.path for fc in self.file_changes]


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERRORED = "errored"


class CheckResult(StrictModel):
    name: str
    command: str
    status: CheckStatus
    exit_code: int | None = None
    duration_seconds: float = 0.0
    output_tail: str = ""
    skip_reason: str | None = None
    required: bool = True

    @property
    def ok(self) -> bool:
        return self.status in (CheckStatus.PASSED, CheckStatus.SKIPPED) or not self.required


class ValidationReport(StrictModel):
    issue_key: str
    platform: Platform
    checks: list[CheckResult] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    attempt: int = 1

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok]

    def failure_summary(self) -> str:
        if self.passed:
            return "All checks passed."
        return "; ".join(f"{c.name} (exit {c.exit_code})" for c in self.failures)


# --------------------------------------------------------------------------- #
# Review / delivery
# --------------------------------------------------------------------------- #


class ReviewSummary(StrictModel):
    issue_key: str
    headline: str
    what_changed: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    reviewer_checklist: list[str] = Field(default_factory=list)
    requirements_coverage: dict[str, str] = Field(
        default_factory=dict, description="requirement id -> covered | partial | missing"
    )
    commit_message: str = ""
    pr_title: str = ""
    pr_body: str = ""


class DeliveryResult(StrictModel):
    branch: str
    base_branch: str
    commit_sha: str | None = None
    pushed: bool = False
    pr_url: str | None = None
    provider: str = "none"
    dry_run: bool = False


# --------------------------------------------------------------------------- #
# Approvals & audit
# --------------------------------------------------------------------------- #


class Decision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class ApprovalRecord(StrictModel):
    gate: str
    decision: Decision
    actor: str = "developer"
    comment: str | None = None
    at: datetime = Field(default_factory=utcnow)


class AuditEvent(StrictModel):
    at: datetime = Field(default_factory=utcnow)
    run_id: str
    actor: str = Field(default="orchestrator", description="agent name, cli, or developer")
    event: str
    detail: dict[str, Any] = Field(default_factory=dict)
