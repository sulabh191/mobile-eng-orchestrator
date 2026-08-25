"""A thin, safe wrapper over the ``git`` CLI.

Only the operations the orchestrator actually needs are exposed, and the
destructive ones (``commit``, ``push``) are separated from the read-only ones so
the delivery agent can be reviewed at a glance. Nothing here force-pushes,
rebases, resets or deletes branches — by design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from orchestrator.core.errors import RepositoryError
from orchestrator.core.models import FileChange
from orchestrator.core.process import CommandResult, run_command, which

_CREDENTIAL_IN_URL = re.compile(r"//[^/@]+@")


def sanitize_remote(url: str | None) -> str | None:
    """Strip any embedded credentials from a remote URL before display/logging."""
    if not url:
        return None
    return _CREDENTIAL_IN_URL.sub("//", url)


@dataclass
class GitStatusEntry:
    index_status: str
    worktree_status: str
    path: str

    @property
    def change_type(self) -> str:
        code = (self.index_status + self.worktree_status).strip()
        if "D" in code:
            return "deleted"
        if "R" in code:
            return "renamed"
        if "A" in code or "?" in code:
            return "added"
        return "modified"


class GitRepo:
    """Read/write access to one git working tree."""

    def __init__(self, path: Path | str, *, timeout: float = 120.0) -> None:
        self.path = Path(path).resolve()
        self.timeout = timeout

    # -- plumbing ----------------------------------------------------------- #

    def _git(self, *args: str, timeout: float | None = None) -> CommandResult:
        return run_command(
            ["git", *args], cwd=self.path, timeout=timeout or self.timeout
        )

    @staticmethod
    def available() -> bool:
        return which("git") is not None

    # -- read-only ---------------------------------------------------------- #

    @property
    def is_repo(self) -> bool:
        if not self.path.exists():
            return False
        return self._git("rev-parse", "--is-inside-work-tree").ok

    def require_repo(self) -> None:
        if not GitRepo.available():
            raise RepositoryError(
                "git is not installed or not on PATH.", hint="Install git and retry."
            )
        if not self.is_repo:
            raise RepositoryError(
                f"{self.path} is not a git repository.",
                hint="Point the orchestrator at a checked-out project.",
            )

    def current_branch(self) -> str | None:
        result = self._git("rev-parse", "--abbrev-ref", "HEAD")
        branch = result.stdout.strip()
        return branch if result.ok and branch and branch != "HEAD" else None

    def head_sha(self) -> str | None:
        result = self._git("rev-parse", "HEAD")
        return result.stdout.strip() if result.ok else None

    def remote_url(self, remote: str = "origin") -> str | None:
        result = self._git("remote", "get-url", remote)
        return result.stdout.strip() if result.ok else None

    def remotes(self) -> list[str]:
        result = self._git("remote")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def default_branch(self, remote: str = "origin") -> str | None:
        result = self._git("symbolic-ref", "--quiet", f"refs/remotes/{remote}/HEAD")
        if result.ok and result.stdout.strip():
            return result.stdout.strip().rsplit("/", 1)[-1]
        for candidate in ("main", "master", "develop"):
            if self._git("rev-parse", "--verify", "--quiet", candidate).ok:
                return candidate
        return None

    def status(self) -> list[GitStatusEntry]:
        result = self._git("status", "--porcelain=v1", "--untracked-files=all")
        entries: list[GitStatusEntry] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            entries.append(GitStatusEntry(line[0], line[1], line[3:].strip().strip('"')))
        return entries

    def is_dirty(self) -> bool:
        return bool(self.status())

    def ahead_behind(self, upstream: str | None = None) -> tuple[int, int]:
        branch = self.current_branch()
        if not branch:
            return (0, 0)
        target = upstream or f"origin/{branch}"
        result = self._git("rev-list", "--left-right", "--count", f"{target}...HEAD")
        if not result.ok:
            return (0, 0)
        parts = result.stdout.split()
        if len(parts) != 2:
            return (0, 0)
        behind, ahead = int(parts[0]), int(parts[1])
        return (ahead, behind)

    def diff_stat(self, base: str | None = None) -> list[FileChange]:
        """Numeric diff stats for working tree (or against ``base`` if given)."""
        args = ["diff", "--numstat"]
        if base:
            args.append(base)
        tracked = self._git(*args)
        changes: dict[str, FileChange] = {}
        for line in tracked.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added, removed, path = parts
            changes[path] = FileChange(
                path=path,
                change_type="modified",
                lines_added=int(added) if added.isdigit() else 0,
                lines_removed=int(removed) if removed.isdigit() else 0,
            )
        for entry in self.status():
            if entry.path not in changes:
                changes[entry.path] = FileChange(path=entry.path, change_type=entry.change_type)
            else:
                changes[entry.path].change_type = entry.change_type
        return sorted(changes.values(), key=lambda c: c.path)

    def diff_text(self, base: str | None = None, *, max_chars: int = 60_000) -> str:
        args = ["diff", "--unified=3"]
        if base:
            args.append(base)
        result = self._git(*args)
        text = result.stdout
        return text if len(text) <= max_chars else text[:max_chars] + "\n…(diff truncated)…"

    def log_since(self, base: str, *, limit: int = 50) -> list[str]:
        result = self._git("log", "--oneline", f"-{limit}", f"{base}..HEAD")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def branch_exists(self, name: str) -> bool:
        return self._git("rev-parse", "--verify", "--quiet", f"refs/heads/{name}").ok

    # -- mutating ------------------------------------------------------------ #

    def create_branch(self, name: str, *, base: str | None = None) -> CommandResult:
        args = ["checkout", "-b", name]
        if base:
            args.append(base)
        return self._git(*args)

    def checkout(self, name: str) -> CommandResult:
        return self._git("checkout", name)

    def stage(self, paths: list[str] | None = None) -> CommandResult:
        return self._git("add", "--", *(paths or ["."]))

    def commit(self, message: str, *, sign: bool = False) -> CommandResult:
        args = ["commit", "-m", message]
        if sign:
            args.append("-S")
        return self._git(*args)

    def push(self, *, remote: str = "origin", branch: str | None = None) -> CommandResult:
        target = branch or self.current_branch()
        if not target:
            raise RepositoryError("Cannot push from a detached HEAD.")
        return self._git("push", "--set-upstream", remote, target, timeout=300.0)
