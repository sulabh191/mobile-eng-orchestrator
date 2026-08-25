"""Validation: check plan assembly, guard checks and the runner."""

from __future__ import annotations

from orchestrator.core.models import CheckStatus, FileChange, ImplementationResult, Platform
from orchestrator.inspection.detector import inspect_repository
from orchestrator.validation.base import (
    Check,
    CheckContext,
    ValidationRunner,
    build_check_plan,
)


def _implementation(paths: list[str]) -> ImplementationResult:
    return ImplementationResult(
        issue_key="MOB-101", file_changes=[FileChange(path=p) for p in paths]
    )


def test_ios_plan_includes_lint_and_build(ios_repo, settings):
    profile = inspect_repository(ios_repo)
    plan = build_check_plan(profile, settings)
    names = plan.names()
    assert "ios:swiftlint" in names
    assert any(name.startswith("ios:xcodebuild") for name in names)
    assert names.index("guard:protected-paths") < names.index("ios:swiftlint")


def test_android_plan_includes_gradle_tasks(android_repo, settings):
    profile = inspect_repository(android_repo)
    names = build_check_plan(profile, settings).names()
    assert "android:assemble" in names
    assert "android:unit-tests" in names
    assert "android:detekt" in names


def test_skip_list_is_respected(android_repo, settings):
    settings.validation.skip = ["android:detekt"]
    names = build_check_plan(inspect_repository(android_repo), settings).names()
    assert "android:detekt" not in names


def test_extra_checks_are_appended(generic_repo, settings):
    settings.validation.extra_checks = [
        {"name": "custom:hello", "command": ["true"], "required": False}
    ]
    names = build_check_plan(inspect_repository(generic_repo), settings).names()
    assert "custom:hello" in names


def test_protected_path_check_fails_on_credentials(generic_repo, settings):
    profile = inspect_repository(generic_repo)
    ctx = CheckContext(profile, settings, _implementation(["app/.env"]))
    from orchestrator.validation.base import _protected_paths_check

    passed, message = _protected_paths_check(ctx)
    assert not passed
    assert ".env" in message


def test_blast_radius_check_enforces_cap(generic_repo, settings):
    settings.behaviour.max_touched_files = 2
    profile = inspect_repository(generic_repo)
    ctx = CheckContext(profile, settings, _implementation(["a", "b", "c"]))
    from orchestrator.validation.base import _blast_radius_check

    passed, message = _blast_radius_check(ctx)
    assert not passed
    assert "cap of 2" in message


def test_conflict_marker_check(generic_repo, settings):
    (generic_repo / "broken.txt").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> other\n")
    profile = inspect_repository(generic_repo)
    ctx = CheckContext(profile, settings, _implementation(["broken.txt"]))
    from orchestrator.validation.base import _no_merge_conflict_markers

    passed, _ = _no_merge_conflict_markers(ctx)
    assert not passed


def test_runner_reports_pass_fail_and_skip(generic_repo, settings):
    profile = inspect_repository(generic_repo)
    plan = build_check_plan(profile, settings)
    plan.checks = [
        Check(name="ok", command=["true"], category="test"),
        Check(name="bad", command=["false"], category="test"),
        Check(
            name="skipped",
            command=["true"],
            precondition=lambda ctx: (False, "not applicable here"),
        ),
    ]
    report = ValidationRunner(profile, settings).run(plan, issue_key="MOB-1")
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["ok"] is CheckStatus.PASSED
    assert statuses["bad"] is CheckStatus.FAILED
    assert statuses["skipped"] is CheckStatus.SKIPPED
    assert not report.passed
    assert "bad" in report.failure_summary()


def test_optional_failure_does_not_fail_the_report(generic_repo, settings):
    profile = inspect_repository(generic_repo)
    plan = build_check_plan(profile, settings)
    plan.checks = [Check(name="optional", command=["false"], required=False)]
    report = ValidationRunner(profile, settings).run(plan, issue_key="MOB-1")
    assert report.passed


def test_fail_fast_stops_early(generic_repo, settings):
    settings.validation.fail_fast = True
    profile = inspect_repository(generic_repo)
    plan = build_check_plan(profile, settings)
    plan.checks = [
        Check(name="bad", command=["false"]),
        Check(name="never-runs", command=["true"]),
    ]
    report = ValidationRunner(profile, settings).run(plan, issue_key="MOB-1")
    assert [c.name for c in report.checks] == ["bad"]


def test_missing_binary_becomes_a_failed_check_not_a_crash(generic_repo, settings):
    profile = inspect_repository(generic_repo)
    plan = build_check_plan(profile, settings)
    plan.checks = [Check(name="nope", command=["definitely-not-a-real-binary-xyz"])]
    report = ValidationRunner(profile, settings).run(plan, issue_key="MOB-1")
    assert report.checks[0].status is CheckStatus.FAILED
    assert report.checks[0].exit_code == 127


def test_platform_is_carried_into_the_report(ios_repo, settings):
    profile = inspect_repository(ios_repo)
    plan = build_check_plan(profile, settings)
    plan.checks = []
    report = ValidationRunner(profile, settings).run(plan, issue_key="MOB-1")
    assert report.platform is Platform.IOS
