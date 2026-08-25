"""Claude Agent SDK backend (optional).

Selected with ``engine.backend = agent_sdk``. Runs the same bounded tasks as the
Claude Code backend but in-process, which is the better fit for CI where the
``claude`` CLI may not be installed. The SDK is an optional dependency:
``pip install 'engineering-orchestrator[sdk]'``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from orchestrator.core.config import EngineSettings
from orchestrator.core.errors import EngineError, EngineUnavailableError
from orchestrator.core.logging import get_logger
from orchestrator.engine.base import Engine, EngineMode, EngineRequest, EngineResponse
from orchestrator.engine.claude_code import EDIT_TOOLS, READ_ONLY_TOOLS

logger = get_logger("engine.agent_sdk")


class AgentSDKEngine(Engine):
    name = "agent_sdk"
    supports_editing = True

    def __init__(self, settings: EngineSettings) -> None:
        self.settings = settings

    def available(self) -> tuple[bool, str]:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            return False, (
                "claude-agent-sdk is not installed. "
                "Install with `pip install 'engineering-orchestrator[sdk]'`."
            )
        return True, "claude-agent-sdk importable"

    def complete(self, request: EngineRequest) -> EngineResponse:
        ok, reason = self.available()
        if not ok:
            raise EngineUnavailableError(reason)
        started = time.monotonic()
        text = asyncio.run(self._run(request))
        return EngineResponse(text=text, duration_seconds=time.monotonic() - started)

    async def _run(self, request: EngineRequest) -> str:
        from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore[import-not-found]

        tools = EDIT_TOOLS if request.mode is EngineMode.EDIT else READ_ONLY_TOOLS
        options = ClaudeAgentOptions(
            cwd=str(request.cwd) if request.cwd else None,
            allowed_tools=tools,
            permission_mode="acceptEdits" if request.mode is EngineMode.EDIT else "plan",
            model=self.settings.model,
            system_prompt=request.system or None,
            max_turns=request.max_turns,
        )

        prompt = request.prompt
        if request.response_model is not None:
            prompt = f"{self.schema_instruction(request.response_model)}\n\n{prompt}"

        chunks: list[str] = []
        try:
            async for message in query(prompt=prompt, options=options):
                chunks.append(_message_text(message))
        except Exception as exc:  # pragma: no cover - depends on optional dep
            raise EngineError(f"Agent SDK failed on task '{request.task}': {exc}") from exc
        return "\n".join(c for c in chunks if c).strip()


def _message_text(message: Any) -> str:
    """Best-effort text extraction across SDK message shapes."""
    if isinstance(message, str):
        return message
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(str(text))
        return "\n".join(parts)
    result = getattr(message, "result", None)
    return str(result) if result else ""
