"""Workflow state: the enum, the legal transition graph, and durable persistence.

State lives inside the *target* repository under ``.orchestrator/runs/<run_id>/``
so that a run is discoverable by anyone sitting in that checkout, and so the
orchestrator repository itself stays free of per-project data.

Every mutation is written atomically and mirrored into an append-only audit log,
which is what makes ``orc resume`` safe after a crash.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Iterator
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field

from orchestrator.core.errors import StateError
from orchestrator.core.models import (
    ApprovalRecord,
    AuditEvent,
    Decision,
    Platform,
    StrictModel,
    utcnow,
)

STATE_DIRNAME = ".orchestrator"
RUNS_DIRNAME = "runs"
STATE_FILENAME = "state.json"
AUDIT_FILENAME = "audit.jsonl"
ARTIFACTS_DIRNAME = "artifacts"
CURRENT_RUN_POINTER = "CURRENT_RUN"
STATE_SCHEMA_VERSION = 1


class WorkflowStatus(str, Enum):
    INITIALIZED = "INITIALIZED"
    JIRA_FETCHED = "JIRA_FETCHED"
    REQUIREMENTS_REVIEW = "REQUIREMENTS_REVIEW"
    PLAN_GENERATED = "PLAN_GENERATED"
    PLAN_APPROVED = "PLAN_APPROVED"
    IMPLEMENTING = "IMPLEMENTING"
    IMPLEMENTATION_COMPLETE = "IMPLEMENTATION_COMPLETE"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VALIDATION_PASSED = "VALIDATION_PASSED"
    REVIEW_READY = "REVIEW_READY"
    READY_FOR_PR = "READY_FOR_PR"
    PR_CREATED = "PR_CREATED"
    COMPLETED = "COMPLETED"
    # Terminal / off-happy-path
    REJECTED = "REJECTED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in (WorkflowStatus.COMPLETED, WorkflowStatus.REJECTED)


#: The only legal transitions. Anything else raises ``StateError``; this is the
#: guard rail that keeps agents from short-circuiting an approval gate.
TRANSITIONS: dict[WorkflowStatus, set[WorkflowStatus]] = {
    WorkflowStatus.INITIALIZED: {WorkflowStatus.JIRA_FETCHED, WorkflowStatus.FAILED},
    WorkflowStatus.JIRA_FETCHED: {WorkflowStatus.REQUIREMENTS_REVIEW, WorkflowStatus.FAILED},
    WorkflowStatus.REQUIREMENTS_REVIEW: {
        WorkflowStatus.PLAN_GENERATED,
        WorkflowStatus.REJECTED,
        WorkflowStatus.FAILED,
    },
    WorkflowStatus.PLAN_GENERATED: {
        WorkflowStatus.PLAN_APPROVED,
        WorkflowStatus.PLAN_GENERATED,  # regenerate after "changes requested"
        WorkflowStatus.REJECTED,
        WorkflowStatus.FAILED,
    },
    WorkflowStatus.PLAN_APPROVED: {WorkflowStatus.IMPLEMENTING, WorkflowStatus.FAILED},
    WorkflowStatus.IMPLEMENTING: {
        WorkflowStatus.IMPLEMENTATION_COMPLETE,
        WorkflowStatus.FAILED,
    },
    WorkflowStatus.IMPLEMENTATION_COMPLETE: {WorkflowStatus.VALIDATING, WorkflowStatus.FAILED},
    WorkflowStatus.VALIDATING: {
        WorkflowStatus.VALIDATION_PASSED,
        WorkflowStatus.VALIDATION_FAILED,
        WorkflowStatus.FAILED,
    },
    WorkflowStatus.VALIDATION_FAILED: {
        WorkflowStatus.IMPLEMENTING,  # remediation loop
        WorkflowStatus.VALIDATING,  # re-run checks after a manual fix
        WorkflowStatus.REJECTED,
        WorkflowStatus.FAILED,
    },
    WorkflowStatus.VALIDATION_PASSED: {WorkflowStatus.REVIEW_READY, WorkflowStatus.FAILED},
    WorkflowStatus.REVIEW_READY: {
        WorkflowStatus.READY_FOR_PR,
        WorkflowStatus.IMPLEMENTING,  # reviewer asked for changes
        WorkflowStatus.REJECTED,
        WorkflowStatus.FAILED,
    },
    WorkflowStatus.READY_FOR_PR: {
        WorkflowStatus.PR_CREATED,
        WorkflowStatus.COMPLETED,  # local-only delivery (commit without PR)
        WorkflowStatus.REJECTED,
        WorkflowStatus.FAILED,
    },
    WorkflowStatus.PR_CREATED: {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED},
    WorkflowStatus.COMPLETED: set(),
    WorkflowStatus.REJECTED: set(),
    # Populated below: a failed run is resumable and may re-enter any phase.
    WorkflowStatus.FAILED: set(),
}

#: Recovery edge. `orc resume` puts a FAILED run back into the phase it died in,
#: so every non-terminal status is reachable from FAILED.
TRANSITIONS[WorkflowStatus.FAILED] = {s for s in WorkflowStatus if s is not WorkflowStatus.FAILED}


def can_transition(src: WorkflowStatus, dst: WorkflowStatus) -> bool:
    return dst in TRANSITIONS.get(src, set())


class StatusChange(StrictModel):
    at: datetime = Field(default_factory=utcnow)
    from_status: WorkflowStatus | None = None
    to_status: WorkflowStatus
    note: str | None = None


class WorkflowState(StrictModel):
    """The complete, serialisable state of one orchestration run."""

    schema_version: int = STATE_SCHEMA_VERSION
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    issue_key: str
    repo_path: str
    platform: Platform = Platform.GENERIC
    status: WorkflowStatus = WorkflowStatus.INITIALIZED
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    branch: str | None = None
    base_branch: str | None = None
    engine: str = "mock"
    dry_run: bool = False
    auto_approve: bool = False

    history: list[StatusChange] = Field(default_factory=list)
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    #: artifact name -> path relative to the run directory
    artifacts: dict[str, str] = Field(default_factory=dict)
    #: per-phase retry counters (e.g. remediation attempts after validation failure)
    attempts: dict[str, int] = Field(default_factory=dict)
    last_error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # -- transitions ------------------------------------------------------- #

    def transition_to(self, dst: WorkflowStatus, *, note: str | None = None) -> None:
        if dst is not self.status and not can_transition(self.status, dst):
            raise StateError(
                f"Illegal transition {self.status.value} -> {dst.value}.",
                hint="Run `orc status` to inspect the run, or `orc reset` to start over.",
            )
        self.history.append(StatusChange(from_status=self.status, to_status=dst, note=note))
        self.status = dst
        self.updated_at = utcnow()
        if dst is not WorkflowStatus.FAILED:
            self.last_error = None

    def record_approval(
        self, gate: str, decision: Decision, *, actor: str = "developer", comment: str | None = None
    ) -> ApprovalRecord:
        record = ApprovalRecord(gate=gate, decision=decision, actor=actor, comment=comment)
        self.approvals.append(record)
        self.updated_at = utcnow()
        return record

    def latest_approval(self, gate: str) -> ApprovalRecord | None:
        for record in reversed(self.approvals):
            if record.gate == gate:
                return record
        return None

    def is_gate_approved(self, gate: str) -> bool:
        record = self.latest_approval(gate)
        return record is not None and record.decision is Decision.APPROVED

    def bump_attempt(self, phase: str) -> int:
        self.attempts[phase] = self.attempts.get(phase, 0) + 1
        return self.attempts[phase]


class RunPaths:
    """Filesystem layout for a single run."""

    def __init__(self, repo_path: Path, run_id: str) -> None:
        self.repo_path = repo_path
        self.run_id = run_id
        self.root = repo_path / STATE_DIRNAME / RUNS_DIRNAME / run_id

    @property
    def state_file(self) -> Path:
        return self.root / STATE_FILENAME

    @property
    def audit_file(self) -> Path:
        return self.root / AUDIT_FILENAME

    @property
    def artifacts_dir(self) -> Path:
        return self.root / ARTIFACTS_DIRNAME

    def artifact(self, name: str) -> Path:
        return self.artifacts_dir / name

    def ensure(self) -> None:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):  # pragma: no cover - only on failure paths
            os.unlink(tmp)


class StateStore:
    """Loads, saves and lists runs for one target repository."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = Path(repo_path).resolve()

    # -- layout ------------------------------------------------------------ #

    @property
    def base_dir(self) -> Path:
        return self.repo_path / STATE_DIRNAME

    @property
    def runs_dir(self) -> Path:
        return self.base_dir / RUNS_DIRNAME

    def paths(self, run_id: str) -> RunPaths:
        return RunPaths(self.repo_path, run_id)

    # -- create / read / write --------------------------------------------- #

    def create(self, state: WorkflowState) -> WorkflowState:
        paths = self.paths(state.run_id)
        if paths.state_file.exists():
            raise StateError(f"Run {state.run_id} already exists.")
        paths.ensure()
        state.history.append(StatusChange(to_status=state.status, note="run created"))
        self.save(state)
        self.set_current(state.run_id)
        self.append_audit(
            AuditEvent(
                run_id=state.run_id,
                actor="cli",
                event="run.created",
                detail={
                    "issue_key": state.issue_key,
                    "platform": state.platform.value,
                    "repo": str(self.repo_path),
                },
            )
        )
        return state

    def save(self, state: WorkflowState) -> None:
        state.updated_at = utcnow()
        paths = self.paths(state.run_id)
        paths.ensure()
        _atomic_write(paths.state_file, state.model_dump_json(indent=2))
        self._ensure_ignored()

    def load(self, run_id: str) -> WorkflowState:
        paths = self.paths(run_id)
        if not paths.state_file.exists():
            raise StateError(
                f"No run '{run_id}' in {self.repo_path}.",
                hint="List runs with `orc status --all`.",
            )
        try:
            data = json.loads(paths.state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateError(f"Corrupt state file for run {run_id}: {exc}") from exc
        version = data.get("schema_version", 0)
        if version > STATE_SCHEMA_VERSION:
            raise StateError(
                f"Run {run_id} was written by a newer orchestrator "
                f"(schema {version} > {STATE_SCHEMA_VERSION})."
            )
        return WorkflowState.model_validate(data)

    def list_runs(self) -> list[WorkflowState]:
        if not self.runs_dir.exists():
            return []
        runs: list[WorkflowState] = []
        for child in sorted(self.runs_dir.iterdir()):
            if (child / STATE_FILENAME).exists():
                try:
                    runs.append(self.load(child.name))
                except StateError:
                    continue
        return sorted(runs, key=lambda s: s.updated_at, reverse=True)

    def find_by_issue(self, issue_key: str) -> WorkflowState | None:
        for state in self.list_runs():
            if state.issue_key.upper() == issue_key.upper() and not state.status.is_terminal:
                return state
        return None

    # -- current-run pointer ------------------------------------------------ #

    def set_current(self, run_id: str) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / CURRENT_RUN_POINTER).write_text(run_id, encoding="utf-8")

    def current_run_id(self) -> str | None:
        pointer = self.base_dir / CURRENT_RUN_POINTER
        if pointer.exists():
            value = pointer.read_text(encoding="utf-8").strip()
            if value and self.paths(value).state_file.exists():
                return value
        runs = self.list_runs()
        return runs[0].run_id if runs else None

    def resolve(self, run_id: str | None) -> WorkflowState:
        target = run_id or self.current_run_id()
        if not target:
            raise StateError(
                f"No orchestrator runs found in {self.repo_path}.",
                hint="Start one with `orc run <ISSUE-KEY>`.",
            )
        return self.load(target)

    # -- artifacts ---------------------------------------------------------- #

    def write_artifact(self, state: WorkflowState, name: str, content: str | dict[str, Any]) -> Path:
        paths = self.paths(state.run_id)
        paths.ensure()
        target = paths.artifact(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_text(json.dumps(content, indent=2, default=str), encoding="utf-8")
        state.artifacts[name] = str(target.relative_to(paths.root))
        return target

    def read_artifact(self, state: WorkflowState, name: str) -> str | None:
        rel = state.artifacts.get(name)
        if not rel:
            return None
        path = self.paths(state.run_id).root / rel
        return path.read_text(encoding="utf-8") if path.exists() else None

    # -- audit -------------------------------------------------------------- #

    def append_audit(self, event: AuditEvent) -> None:
        paths = self.paths(event.run_id)
        paths.ensure()
        with paths.audit_file.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")

    def read_audit(self, run_id: str) -> Iterator[AuditEvent]:
        path = self.paths(run_id).audit_file
        if not path.exists():
            return iter(())

        def _iter() -> Iterator[AuditEvent]:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        yield AuditEvent.model_validate_json(line)

        return _iter()

    # -- housekeeping -------------------------------------------------------- #

    def _ensure_ignored(self) -> None:
        """Keep orchestrator run state out of the target repository's commits."""
        exclude = self.repo_path / ".git" / "info" / "exclude"
        entry = f"/{STATE_DIRNAME}/"
        try:
            if exclude.parent.exists():
                existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
                if entry not in existing:
                    with exclude.open("a", encoding="utf-8") as handle:
                        handle.write(
                            f"\n# added by engineering-orchestrator\n{entry}\n"
                        )
        except OSError:  # pragma: no cover - best effort only
            pass
