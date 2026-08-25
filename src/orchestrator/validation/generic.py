"""Checks that apply to any repository, plus user-configured extras."""

from __future__ import annotations

from orchestrator.core.config import Settings
from orchestrator.core.process import run_command
from orchestrator.inspection.profile import RepoProfile
from orchestrator.validation.base import Check, CheckContext


def _git_tree_sane(ctx: CheckContext) -> tuple[bool, str]:
    """The working tree must contain changes, and must not be mid-merge."""
    root = ctx.profile.path
    if (root / ".git" / "MERGE_HEAD").exists():
        return False, "Repository is mid-merge; resolve it before continuing."
    if (root / ".git" / "rebase-merge").exists() or (root / ".git" / "rebase-apply").exists():
        return False, "Repository is mid-rebase; finish or abort it before continuing."
    result = run_command(["git", "status", "--porcelain"], cwd=root, timeout=60)
    if not result.ok:
        return False, f"git status failed: {result.tail(500)}"
    if not result.stdout.strip():
        return False, "No changes in the working tree — the implementation produced nothing."
    return True, f"{len(result.stdout.strip().splitlines())} changed path(s)."


def _no_large_files(ctx: CheckContext) -> tuple[bool, str]:
    limit_mb = 5
    offenders: list[str] = []
    if ctx.implementation is None:
        return True, "No implementation result to inspect."
    for rel in ctx.implementation.touched_paths:
        path = ctx.profile.path / rel
        if path.is_file() and path.stat().st_size > limit_mb * 1024 * 1024:
            offenders.append(f"{rel} ({path.stat().st_size // (1024 * 1024)}MB)")
    if offenders:
        return False, f"Files larger than {limit_mb}MB were added: " + ", ".join(offenders)
    return True, "No oversized files."


def generic_checks(profile: RepoProfile, settings: Settings) -> list[Check]:
    checks: list[Check] = [
        Check(
            name="repo:working-tree",
            description="The working tree has changes and is not mid-merge or mid-rebase.",
            python=_git_tree_sane,
            category="guard",
            timeout=60,
        ),
        Check(
            name="repo:no-large-files",
            description="No oversized binaries were introduced.",
            python=_no_large_files,
            category="guard",
            required=False,
            timeout=60,
        ),
    ]

    for index, extra in enumerate(settings.validation.extra_checks, start=1):
        command = extra.get("command")
        if not command:
            continue
        checks.append(
            Check(
                name=str(extra.get("name", f"custom:{index}")),
                description=str(extra.get("description", "Project-defined check.")),
                command=command if isinstance(command, list) else ["bash", "-lc", str(command)],
                required=bool(extra.get("required", True)),
                category="custom",
                timeout=float(extra.get("timeout", 900)),
            )
        )
    return checks
