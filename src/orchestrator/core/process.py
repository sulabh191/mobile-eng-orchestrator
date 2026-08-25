"""A single, audited way to run external commands.

Every shell-out in the orchestrator goes through :func:`run_command`:

* commands are always argument lists (never a shell string), so nothing the
  issue tracker or the engine produces can be interpolated into a shell;
* output is captured, size-bounded and tail-truncated so a runaway build log
  cannot blow up memory or the audit file;
* timeouts are mandatory and produce a structured result rather than an
  exception, so validation can report "timed out" as a normal check failure.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.core.logging import get_logger

logger = get_logger("process")

MAX_CAPTURED_CHARS = 200_000
TAIL_CHARS = 8_000


@dataclass
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    cwd: str | None = None
    env_extra: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def display(self) -> str:
        return " ".join(self.command)

    def tail(self, chars: int = TAIL_CHARS) -> str:
        combined = (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()
        if len(combined) <= chars:
            return combined
        return "…(truncated)…\n" + combined[-chars:]


def which(binary: str) -> str | None:
    return shutil.which(binary)


def _truncate(text: str) -> str:
    if len(text) <= MAX_CAPTURED_CHARS:
        return text
    half = MAX_CAPTURED_CHARS // 2
    return text[:half] + "\n…(output truncated by orchestrator)…\n" + text[-half:]


def run_command(
    command: list[str],
    *,
    cwd: Path | str | None = None,
    timeout: float = 600.0,
    env_extra: dict[str, str] | None = None,
    input_text: str | None = None,
    check: bool = False,
) -> CommandResult:
    """Run ``command`` and always return a :class:`CommandResult`."""
    env = os.environ.copy()
    # Keep child processes non-interactive: a hung prompt is worse than a failure.
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("CI", env.get("CI", ""))
    if env_extra:
        env.update(env_extra)

    started = time.monotonic()
    logger.debug("run: %s (cwd=%s)", " ".join(command), cwd)
    try:
        completed = subprocess.run(  # noqa: S603 - argument list, never shell=True
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            input=input_text,
            check=False,
        )
        result = CommandResult(
            command=command,
            exit_code=completed.returncode,
            stdout=_truncate(completed.stdout or ""),
            stderr=_truncate(completed.stderr or ""),
            duration_seconds=time.monotonic() - started,
            cwd=str(cwd) if cwd else None,
            env_extra=dict(env_extra or {}),
        )
    except subprocess.TimeoutExpired as exc:
        result = CommandResult(
            command=command,
            exit_code=124,
            stdout=_truncate(exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")),
            stderr=f"Command timed out after {timeout:.0f}s.",
            duration_seconds=time.monotonic() - started,
            timed_out=True,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        result = CommandResult(
            command=command,
            exit_code=127,
            stderr=str(exc),
            duration_seconds=time.monotonic() - started,
            cwd=str(cwd) if cwd else None,
        )

    if check and not result.ok:
        from orchestrator.core.errors import OrchestratorError

        raise OrchestratorError(
            f"Command failed ({result.exit_code}): {result.display}",
            hint=result.tail(2000) or None,
        )
    return result
