"""Workflow state machine and persistence."""

from __future__ import annotations

import pytest

from orchestrator.core.errors import StateError
from orchestrator.core.models import Decision, Platform
from orchestrator.core.state import (
    StateStore,
    WorkflowState,
    WorkflowStatus,
    can_transition,
)


def _state(repo) -> WorkflowState:
    return WorkflowState(issue_key="MOB-101", repo_path=str(repo), platform=Platform.IOS)


def test_legal_transition_is_recorded(generic_repo):
    state = _state(generic_repo)
    state.transition_to(WorkflowStatus.JIRA_FETCHED, note="fetched")
    assert state.status is WorkflowStatus.JIRA_FETCHED
    assert state.history[-1].from_status is WorkflowStatus.INITIALIZED
    assert state.history[-1].note == "fetched"


def test_illegal_transition_is_refused(generic_repo):
    state = _state(generic_repo)
    with pytest.raises(StateError):
        state.transition_to(WorkflowStatus.PR_CREATED)


def test_gate_cannot_be_skipped(generic_repo):
    """A plan must be approved before implementation can start."""
    assert not can_transition(WorkflowStatus.PLAN_GENERATED, WorkflowStatus.IMPLEMENTING)
    assert can_transition(WorkflowStatus.PLAN_GENERATED, WorkflowStatus.PLAN_APPROVED)
    assert can_transition(WorkflowStatus.PLAN_APPROVED, WorkflowStatus.IMPLEMENTING)


def test_failed_run_can_re_enter_any_phase():
    assert can_transition(WorkflowStatus.FAILED, WorkflowStatus.IMPLEMENTING)
    assert can_transition(WorkflowStatus.FAILED, WorkflowStatus.VALIDATING)


def test_round_trip_persistence(generic_repo):
    store = StateStore(generic_repo)
    state = store.create(_state(generic_repo))
    state.transition_to(WorkflowStatus.JIRA_FETCHED)
    state.record_approval("plan", Decision.APPROVED, comment="looks right")
    store.save(state)

    reloaded = store.load(state.run_id)
    assert reloaded.status is WorkflowStatus.JIRA_FETCHED
    assert reloaded.is_gate_approved("plan")
    assert reloaded.latest_approval("plan").comment == "looks right"


def test_current_run_pointer_and_resolution(generic_repo):
    store = StateStore(generic_repo)
    first = store.create(_state(generic_repo))
    second = store.create(WorkflowState(issue_key="MOB-102", repo_path=str(generic_repo)))
    assert store.current_run_id() == second.run_id
    assert store.resolve(None).run_id == second.run_id
    assert store.resolve(first.run_id).run_id == first.run_id


def test_find_by_issue_ignores_terminal_runs(generic_repo):
    store = StateStore(generic_repo)
    state = store.create(_state(generic_repo))
    assert store.find_by_issue("mob-101").run_id == state.run_id

    state.transition_to(WorkflowStatus.JIRA_FETCHED)
    state.transition_to(WorkflowStatus.REQUIREMENTS_REVIEW)
    state.transition_to(WorkflowStatus.REJECTED)
    store.save(state)
    assert store.find_by_issue("MOB-101") is None


def test_artifacts_and_audit(generic_repo):
    store = StateStore(generic_repo)
    state = store.create(_state(generic_repo))
    store.write_artifact(state, "plan.md", "# plan")
    store.save(state)

    assert store.read_artifact(state, "plan.md") == "# plan"
    events = list(store.read_audit(state.run_id))
    assert any(event.event == "run.created" for event in events)


def test_state_dir_is_git_ignored(generic_repo):
    store = StateStore(generic_repo)
    store.create(_state(generic_repo))
    exclude = (generic_repo / ".git" / "info" / "exclude").read_text()
    assert "/.orchestrator/" in exclude


def test_attempt_counter(generic_repo):
    state = _state(generic_repo)
    assert state.bump_attempt("validation") == 1
    assert state.bump_attempt("validation") == 2
