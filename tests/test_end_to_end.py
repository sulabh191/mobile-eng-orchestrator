"""End-to-end workflow tests.

These exercise the real state machine, real git, real validation runner and
real approval gates — only the reasoning engine and the issue tracker are
offline stand-ins. If this file passes, the orchestration itself works.
"""

from __future__ import annotations

import subprocess

import pytest

from orchestrator.core.errors import ApprovalRequired, ValidationFailed
from orchestrator.core.models import Decision, Platform
from orchestrator.core.state import StateStore, WorkflowStatus
from orchestrator.engine.mock import MockEngine
from orchestrator.integrations.jira.mock import MockJiraClient
from orchestrator.workflow.orchestrator import Orchestrator, RunOptions


def build(repo, settings, **overrides):
    settings.engine.backend = "mock"
    settings.git.provider = overrides.pop("provider", "none")
    settings.behaviour.interactive = False
    # Keep the offline run fast and hermetic: platform builds are not available here.
    settings.validation.skip = [
        "ios:swiftlint",
        "ios:xcodebuild-build",
        "ios:xcodebuild-test",
        "ios:podfile-lock-in-sync",
        "android:assemble",
        "android:unit-tests",
        "android:detekt",
        "android:ktlint",
        "android:lint",
    ]
    orchestrator = Orchestrator(
        settings,
        repo_path=repo,
        engine=overrides.pop("engine", MockEngine()),
        tracker=overrides.pop("tracker", MockJiraClient()),
    )
    options = RunOptions(
        issue_key=overrides.pop("issue_key", "MOB-101"),
        repo_path=repo,
        offline=True,
        interactive=False,
        **overrides,
    )
    return orchestrator, options


def test_run_stops_at_the_plan_gate(ios_repo, settings):
    orchestrator, options = build(ios_repo, settings, dry_run=True)
    with pytest.raises(ApprovalRequired) as exc:
        orchestrator.start(options)

    assert exc.value.gate == "plan"
    state = StateStore(ios_repo).resolve(None)
    assert state.status is WorkflowStatus.PLAN_GENERATED
    # Nothing has been written to the repository yet.
    assert not (ios_repo / "ORCHESTRATOR_MOCK_CHANGES.md").exists()


def test_approve_then_resume_reaches_the_delivery_gate(ios_repo, settings):
    orchestrator, options = build(ios_repo, settings, dry_run=True)
    with pytest.raises(ApprovalRequired):
        orchestrator.start(options)

    store = StateStore(ios_repo)
    state = store.resolve(None)
    state.record_approval("plan", Decision.APPROVED, comment="ship it")
    store.save(state)

    with pytest.raises(ApprovalRequired) as exc:
        orchestrator.resume(state.run_id, options)
    assert exc.value.gate == "delivery"

    state = store.resolve(state.run_id)
    assert state.status is WorkflowStatus.READY_FOR_PR
    assert state.branch and state.branch.startswith("feature/mob-101-")
    assert (ios_repo / "ORCHESTRATOR_MOCK_CHANGES.md").exists()
    assert "requirements.md" in state.artifacts
    assert "plan.md" in state.artifacts
    assert "review.md" in state.artifacts


def test_full_run_with_auto_approval_completes_as_dry_run(ios_repo, settings, monkeypatch):
    monkeypatch.setenv("ORC_I_UNDERSTAND_AUTO_APPROVE", "1")
    orchestrator, options = build(ios_repo, settings, dry_run=True, auto_approve=True)
    state = orchestrator.start(options)

    assert state.status is WorkflowStatus.COMPLETED
    assert state.platform is Platform.IOS
    # dry run: the branch was never created and nothing was committed
    branches = subprocess.run(
        ["git", "branch", "--list"], cwd=ios_repo, capture_output=True, text=True
    ).stdout
    assert "feature/mob-101" not in branches


def test_full_run_commits_and_pushes_to_a_local_remote(ios_repo, settings, tmp_path, monkeypatch):
    monkeypatch.setenv("ORC_I_UNDERSTAND_AUTO_APPROVE", "1")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)], cwd=ios_repo, check=True, capture_output=True
    )

    orchestrator, options = build(ios_repo, settings, auto_approve=True)
    state = orchestrator.start(options)

    assert state.status is WorkflowStatus.COMPLETED
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=ios_repo, capture_output=True, text=True
    ).stdout
    assert "MOB-101" in log or "pull-to-refresh" in log.lower()

    remote_branches = subprocess.run(
        ["git", "branch", "--list"], cwd=remote, capture_output=True, text=True
    ).stdout
    assert "mob-101" in remote_branches


def test_android_repository_runs_through_the_same_pipeline(android_repo, settings, monkeypatch):
    monkeypatch.setenv("ORC_I_UNDERSTAND_AUTO_APPROVE", "1")
    orchestrator, options = build(android_repo, settings, dry_run=True, auto_approve=True)
    state = orchestrator.start(options)
    assert state.status is WorkflowStatus.COMPLETED
    assert state.platform is Platform.ANDROID


def test_stop_after_plan_leaves_the_repository_untouched(ios_repo, settings, monkeypatch):
    monkeypatch.setenv("ORC_I_UNDERSTAND_AUTO_APPROVE", "1")
    orchestrator, options = build(
        ios_repo, settings, dry_run=True, auto_approve=True, stop_after="plan"
    )
    state = orchestrator.start(options)
    # The plan exists and is waiting; the approval gate has not been consumed.
    assert state.status is WorkflowStatus.PLAN_GENERATED
    assert not (ios_repo / "ORCHESTRATOR_MOCK_CHANGES.md").exists()


def test_rejection_stops_the_run_cleanly(ios_repo, settings):
    orchestrator, options = build(ios_repo, settings, dry_run=True)
    with pytest.raises(ApprovalRequired):
        orchestrator.start(options)

    store = StateStore(ios_repo)
    state = store.resolve(None)
    state.record_approval("plan", Decision.REJECTED, comment="wrong approach")
    store.save(state)

    state = orchestrator.resume(state.run_id, options)
    assert state.status is WorkflowStatus.REJECTED
    assert not (ios_repo / "ORCHESTRATOR_MOCK_CHANGES.md").exists()


def test_validation_failure_exhausts_remediation_then_fails(ios_repo, settings, monkeypatch):
    monkeypatch.setenv("ORC_I_UNDERSTAND_AUTO_APPROVE", "1")
    orchestrator, options = build(ios_repo, settings, dry_run=True, auto_approve=True)
    orchestrator.settings.validation.max_remediation_attempts = 1
    orchestrator.settings.validation.extra_checks = [
        {"name": "custom:always-fails", "command": ["false"]}
    ]

    with pytest.raises(ValidationFailed):
        orchestrator.start(options)

    state = StateStore(ios_repo).resolve(None)
    assert state.status is WorkflowStatus.FAILED
    assert state.attempts["remediation"] == 1
    assert state.attempts["validation"] == 2  # original + one remediation


def test_failed_run_resumes_from_the_phase_that_failed(ios_repo, settings, monkeypatch):
    monkeypatch.setenv("ORC_I_UNDERSTAND_AUTO_APPROVE", "1")
    orchestrator, options = build(ios_repo, settings, dry_run=True, auto_approve=True)
    orchestrator.settings.validation.max_remediation_attempts = 0
    orchestrator.settings.validation.extra_checks = [
        {"name": "custom:always-fails", "command": ["false"]}
    ]
    with pytest.raises(ValidationFailed):
        orchestrator.start(options)

    store = StateStore(ios_repo)
    state = store.resolve(None)
    assert state.status is WorkflowStatus.FAILED

    # Fix the environment, then resume: the run picks up at validation.
    orchestrator.settings.validation.extra_checks = []
    state = orchestrator.resume(state.run_id, options)
    assert state.status is WorkflowStatus.COMPLETED


def test_audit_log_records_every_phase(ios_repo, settings, monkeypatch):
    monkeypatch.setenv("ORC_I_UNDERSTAND_AUTO_APPROVE", "1")
    orchestrator, options = build(ios_repo, settings, dry_run=True, auto_approve=True)
    state = orchestrator.start(options)

    events = {event.event for event in StateStore(ios_repo).read_audit(state.run_id)}
    assert {
        "run.created",
        "issue.fetched",
        "requirements.derived",
        "plan.generated",
        "implementation.completed",
        "validation.completed",
        "review.completed",
        "approval.decided",
    } <= events


def test_second_run_for_the_same_issue_resumes_rather_than_duplicating(ios_repo, settings):
    orchestrator, options = build(ios_repo, settings, dry_run=True)
    with pytest.raises(ApprovalRequired):
        orchestrator.start(options)
    with pytest.raises(ApprovalRequired):
        orchestrator.start(options)
    assert len(StateStore(ios_repo).list_runs()) == 1


def test_auto_approve_without_acknowledgement_is_refused(ios_repo, settings):
    from orchestrator.core.errors import ConfigurationError

    orchestrator, options = build(ios_repo, settings, dry_run=True, auto_approve=True)
    with pytest.raises(ConfigurationError):
        orchestrator.start(options)


def test_non_git_directory_is_refused(tmp_path, settings):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    from orchestrator.core.errors import RepositoryError

    orchestrator, options = build(plain, settings, dry_run=True)
    with pytest.raises(RepositoryError):
        orchestrator.start(options)
