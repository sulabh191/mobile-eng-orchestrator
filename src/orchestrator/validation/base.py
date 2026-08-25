"""Check definitions and the runner that executes them."""

from __future__ import annotations

import fnmatch
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.core.config import Settings
from orchestrator.core.logging import get_logger
from orchestrator.core.models import (
    CheckResult,
    CheckStatus,
    ImplementationResult,
    Platform,
    ValidationReport,
    utcnow,
)
from orchestrator.core.process import run_command, which
from orchestrator.inspection.profile import RepoProfile

logger = get_logger("validation")

#: A python check returns ``(passed, message)``.
PythonCheck = Callable[["CheckContext"], tuple[bool, str]]


@dataclass
class CheckContext:
    profile: RepoProfile
    settings: Settings
    implementation: ImplementationResult | None = None


@dataclass
class Check:
    """One validation step: either a shell command or an in-process assertion."""

    name: str
    description: str = ""
    command: list[str] | None = None
    python: PythonCheck | None = None
    required: bool = True
    timeout: float = 1800.0
    #: Returns ``(should_run, skip_reason)``.
    precondition: Callable[[CheckContext], tuple[bool, str]] | None = None
    category: str = "general"

    @property
    def display_command(self) -> str:
        if self.command:
            return " ".join(self.command)
        return f"python:{self.name}"


@dataclass
class CheckPlan:
    platform: Platform
    checks: list[Check] = field(default_factory=list)

    def names(self) -> list[str]:
        return [c.name for c in self.checks]


# --------------------------------------------------------------------------- #
# Universal guard checks (run for every platform, before any build)
# --------------------------------------------------------------------------- #


def _protected_paths_check(ctx: CheckContext) -> tuple[bool, str]:
    """Refuse to proceed if the implementer touched a path it must never write."""
    if ctx.implementation is None:
        return True, "No implementation result to inspect."
    patterns = ctx.settings.behaviour.protected_paths
    offenders = [
        path
        for path in ctx.implementation.touched_paths
        if any(fnmatch.fnmatch(path, pattern) or path.startswith(pattern.rstrip("*")) for pattern in patterns)
    ]
    if offenders:
        return False, "Protected paths were modified: " + ", ".join(sorted(offenders))
    return True, f"{len(ctx.implementation.touched_paths)} changed path(s), none protected."


def _blast_radius_check(ctx: CheckContext) -> tuple[bool, str]:
    """A single ticket that rewrites half the repository is a bug, not a feature."""
    if ctx.implementation is None:
        return True, "No implementation result to inspect."
    cap = ctx.settings.behaviour.max_touched_files
    count = len(ctx.implementation.file_changes)
    if count > cap:
        return False, f"{count} files changed, which exceeds the configured cap of {cap}."
    return True, f"{count} file(s) changed (cap {cap})."


def _no_merge_conflict_markers(ctx: CheckContext) -> tuple[bool, str]:
    if ctx.implementation is None:
        return True, "No implementation result to inspect."
    root = ctx.profile.path
    offenders: list[str] = []
    for rel in ctx.implementation.touched_paths:
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if "<<<<<<<" in text and ">>>>>>>" in text:
            offenders.append(rel)
    if offenders:
        return False, "Conflict markers found in: " + ", ".join(offenders)
    return True, "No conflict markers in changed files."


UNIVERSAL_CHECKS: list[Check] = [
    Check(
        name="guard:protected-paths",
        description="No credential, signing or orchestrator-internal file was modified.",
        python=_protected_paths_check,
        category="guard",
        timeout=30,
    ),
    Check(
        name="guard:blast-radius",
        description="The change stays within the configured file-count cap.",
        python=_blast_radius_check,
        category="guard",
        timeout=30,
    ),
    Check(
        name="guard:conflict-markers",
        description="No unresolved merge-conflict markers were left behind.",
        python=_no_merge_conflict_markers,
        category="guard",
        timeout=30,
    ),
]


def binary_available(binary: str) -> Callable[[CheckContext], tuple[bool, str]]:
    def _precondition(_: CheckContext) -> tuple[bool, str]:
        if which(binary):
            return True, ""
        return False, f"`{binary}` is not installed on this machine."

    return _precondition


def file_exists(relative: str) -> Callable[[CheckContext], tuple[bool, str]]:
    def _precondition(ctx: CheckContext) -> tuple[bool, str]:
        if (ctx.profile.path / relative).exists():
            return True, ""
        return False, f"{relative} not found in the repository."

    return _precondition


def all_of(*preconditions: Callable[[CheckContext], tuple[bool, str]]):
    def _precondition(ctx: CheckContext) -> tuple[bool, str]:
        for check in preconditions:
            ok, reason = check(ctx)
            if not ok:
                return False, reason
        return True, ""

    return _precondition


# --------------------------------------------------------------------------- #
# Plan assembly
# --------------------------------------------------------------------------- #


def build_check_plan(profile: RepoProfile, settings: Settings) -> CheckPlan:
    """Assemble the ordered check list for this repository."""
    from orchestrator.validation.android import android_checks
    from orchestrator.validation.generic import generic_checks
    from orchestrator.validation.ios import ios_checks

    checks: list[Check] = list(UNIVERSAL_CHECKS)
    if profile.platform is Platform.IOS:
        checks += ios_checks(profile, settings)
    elif profile.platform is Platform.ANDROID:
        checks += android_checks(profile, settings)
    checks += generic_checks(profile, settings)

    skip = {name.lower() for name in settings.validation.skip}
    checks = [c for c in checks if c.name.lower() not in skip]
    return CheckPlan(platform=profile.platform, checks=checks)


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


class ValidationRunner:
    """Executes a :class:`CheckPlan` and produces a :class:`ValidationReport`."""

    def __init__(self, profile: RepoProfile, settings: Settings) -> None:
        self.profile = profile
        self.settings = settings

    def run(
        self,
        plan: CheckPlan,
        *,
        issue_key: str,
        implementation: ImplementationResult | None = None,
        attempt: int = 1,
        on_check: Callable[[Check], None] | None = None,
    ) -> ValidationReport:
        ctx = CheckContext(profile=self.profile, settings=self.settings, implementation=implementation)
        report = ValidationReport(issue_key=issue_key, platform=self.profile.platform, attempt=attempt)
        fail_fast = self.settings.validation.fail_fast

        for check in plan.checks:
            if on_check:
                on_check(check)
            report.checks.append(self._run_one(check, ctx))
            if fail_fast and report.checks[-1].status is CheckStatus.FAILED and check.required:
                logger.info("fail-fast: stopping after %s", check.name)
                break

        report.finished_at = utcnow()
        return report

    def _run_one(self, check: Check, ctx: CheckContext) -> CheckResult:
        if check.precondition is not None:
            should_run, reason = check.precondition(ctx)
            if not should_run:
                return CheckResult(
                    name=check.name,
                    command=check.display_command,
                    status=CheckStatus.SKIPPED,
                    skip_reason=reason,
                    required=check.required,
                )

        started = time.monotonic()
        if check.python is not None:
            try:
                passed, message = check.python(ctx)
                status = CheckStatus.PASSED if passed else CheckStatus.FAILED
                exit_code = 0 if passed else 1
                output = message
            except Exception as exc:  # pragma: no cover - defensive
                status, exit_code, output = CheckStatus.ERRORED, 1, f"{type(exc).__name__}: {exc}"
            return CheckResult(
                name=check.name,
                command=check.display_command,
                status=status,
                exit_code=exit_code,
                duration_seconds=time.monotonic() - started,
                output_tail=output[:4000],
                required=check.required,
            )

        assert check.command is not None
        result = run_command(
            check.command,
            cwd=self.profile.path,
            timeout=min(check.timeout, self.settings.validation.per_check_timeout_seconds),
        )
        return CheckResult(
            name=check.name,
            command=result.display,
            status=CheckStatus.PASSED if result.ok else CheckStatus.FAILED,
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
            output_tail=result.tail(),
            required=check.required,
        )


def resolve_path(profile: RepoProfile, relative: str) -> Path:
    return profile.path / relative
