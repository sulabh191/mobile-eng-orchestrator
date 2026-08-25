"""Claude Code backend.

Drives the ``claude`` CLI in headless mode (``-p``). The orchestrator stays in
control of the workflow; Claude Code is used for exactly one bounded task at a
time, with an explicit tool allowlist per mode:

* READ_ONLY — Read/Grep/Glob only. Cannot modify the repository, so the
  requirements, planning and review agents are structurally incapable of
  editing code.
* EDIT — adds Edit/Write/MultiEdit and a narrow Bash allowance, and is only
  ever used after the plan gate has been approved.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from orchestrator.core.config import EngineSettings
from orchestrator.core.errors import EngineError, EngineUnavailableError
from orchestrator.core.logging import get_logger
from orchestrator.core.process import run_command, which
from orchestrator.engine.base import Engine, EngineMode, EngineRequest, EngineResponse

logger = get_logger("engine.claude_code")

READ_ONLY_TOOLS = ["Read", "Grep", "Glob"]
EDIT_TOOLS = [*READ_ONLY_TOOLS, "Edit", "Write", "MultiEdit", "NotebookEdit"]


class ClaudeCodeEngine(Engine):
    name = "claude_code"
    supports_editing = True

    def __init__(self, settings: EngineSettings) -> None:
        self.settings = settings
        self.binary = settings.claude_binary

    # -- availability ---------------------------------------------------------- #

    def available(self) -> tuple[bool, str]:
        path = which(self.binary)
        if not path:
            return False, (
                f"`{self.binary}` was not found on PATH. Install Claude Code "
                "(https://claude.com/claude-code) or set engine.claude_binary."
            )
        version = run_command([self.binary, "--version"], timeout=30)
        if not version.ok:
            return False, f"`{self.binary} --version` failed: {version.tail(300)}"
        return True, version.stdout.strip() or path

    # -- execution ------------------------------------------------------------- #

    def complete(self, request: EngineRequest) -> EngineResponse:
        ok, reason = self.available()
        if not ok:
            raise EngineUnavailableError(reason)

        tools = EDIT_TOOLS if request.mode is EngineMode.EDIT else READ_ONLY_TOOLS
        args = [
            self.binary,
            "-p",
            self._compose(request),
            "--output-format",
            "json",
            "--allowedTools",
            ",".join(tools),
        ]
        if request.mode is EngineMode.READ_ONLY:
            # Belt and braces: even if the allowlist were bypassed, refuse writes.
            args += ["--permission-mode", "plan"]
        else:
            args += ["--permission-mode", "acceptEdits"]
        if self.settings.model:
            args += ["--model", self.settings.model]
        if request.max_turns:
            args += ["--max-turns", str(request.max_turns)]
        args += self.settings.extra_args

        started = time.monotonic()
        result = run_command(
            args,
            cwd=request.cwd,
            timeout=request.timeout or self.settings.timeout_seconds,
        )
        duration = time.monotonic() - started

        if result.timed_out:
            raise EngineError(
                f"Claude Code timed out after {request.timeout or self.settings.timeout_seconds:.0f}s "
                f"on task '{request.task}'.",
                hint="Raise engine.timeout_seconds or narrow the plan step.",
            )
        if not result.ok:
            raise EngineError(
                f"Claude Code failed on task '{request.task}' (exit {result.exit_code}).",
                hint=result.tail(1500),
            )

        return self._parse(result.stdout, duration)

    # -- helpers ----------------------------------------------------------------- #

    def _compose(self, request: EngineRequest) -> str:
        parts = []
        if request.system:
            parts.append(request.system.strip())
        if request.response_model is not None:
            parts.append(self.schema_instruction(request.response_model))
        parts.append(request.prompt.strip())
        return "\n\n---\n\n".join(parts)

    def _parse(self, stdout: str, duration: float) -> EngineResponse:
        raw: dict = {}
        text = stdout.strip()
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None

        if isinstance(payload, dict):
            raw = payload
            text = str(payload.get("result") or payload.get("text") or stdout)
        elif isinstance(payload, list) and payload:
            raw = {"messages": payload}
            last = payload[-1]
            text = str(last.get("result") or last.get("text") or stdout)

        return EngineResponse(
            text=text,
            raw=raw,
            duration_seconds=duration,
            cost_usd=raw.get("total_cost_usd"),
            turns=int(raw.get("num_turns", 1) or 1),
        )

    @staticmethod
    def workspace_hint(repo: Path) -> str:
        return f"You are working inside the repository at {repo}."
