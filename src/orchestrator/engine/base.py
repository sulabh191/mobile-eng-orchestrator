"""The engine contract plus schema-validated structured output."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from orchestrator.core.errors import StructuredOutputError
from orchestrator.core.logging import get_logger

logger = get_logger("engine")

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class EngineMode(str, Enum):
    """How much the engine is allowed to do."""

    #: Read and reason only. Used for requirements, planning and review.
    READ_ONLY = "read_only"
    #: May edit files inside the target repository. Used only by the implementer,
    #: only after the plan gate has been approved.
    EDIT = "edit"


@dataclass
class EngineRequest:
    task: str
    prompt: str
    system: str = ""
    mode: EngineMode = EngineMode.READ_ONLY
    cwd: Path | None = None
    response_model: type[BaseModel] | None = None
    max_turns: int | None = None
    timeout: float | None = None
    #: Free-form context carried through to stub/mock engines and transcripts.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineResponse:
    text: str
    structured: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0
    cost_usd: float | None = None
    turns: int = 1


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model response.

    Handles bare JSON, fenced blocks, and prose-wrapped JSON, because engines
    are not reliably terse no matter how firmly you ask.
    """
    candidate = text.strip()
    if not candidate:
        return None
    for attempt in (candidate, *(m.group(1).strip() for m in _FENCE.finditer(candidate))):
        try:
            loaded = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            loaded = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
        if isinstance(loaded, dict):
            return loaded
    return None


class Engine(ABC):
    """Base class for every backend."""

    name: str = "engine"
    #: Whether this backend can edit files in the target repository.
    supports_editing: bool = False

    @abstractmethod
    def complete(self, request: EngineRequest) -> EngineResponse:
        """Run one engine turn (or agent loop) and return its output."""

    def available(self) -> tuple[bool, str]:
        """``(is_available, reason)`` — surfaced by `orc doctor`."""
        return True, "ready"

    # -- structured output ---------------------------------------------------- #

    def generate_structured(
        self, request: EngineRequest, model_cls: type[T], *, retries: int = 1
    ) -> tuple[T, EngineResponse]:
        """Run the request and validate the result against ``model_cls``.

        On a schema violation the engine is asked once more with the validation
        errors appended — the single most effective repair strategy, and cheaper
        than a bespoke parser.
        """
        request.response_model = model_cls
        attempt = 0
        last_error: str = ""
        current = request
        while attempt <= retries:
            response = self.complete(current)
            payload = response.structured or extract_json(response.text)
            if payload is not None:
                try:
                    return model_cls.model_validate(payload), response
                except ValidationError as exc:
                    last_error = exc.json(indent=2)
            else:
                last_error = "No JSON object found in the response."

            attempt += 1
            if attempt > retries:
                break
            logger.warning(
                "%s: schema validation failed for task '%s', retrying", self.name, request.task
            )
            current = EngineRequest(
                task=request.task,
                prompt=(
                    f"{request.prompt}\n\n"
                    "Your previous response did not satisfy the required JSON schema.\n"
                    f"Errors:\n{last_error}\n\n"
                    "Reply with ONLY the corrected JSON object. No prose, no code fences."
                ),
                system=request.system,
                mode=request.mode,
                cwd=request.cwd,
                response_model=model_cls,
                max_turns=request.max_turns,
                timeout=request.timeout,
                metadata=request.metadata,
            )

        raise StructuredOutputError(
            f"{self.name} could not produce valid {model_cls.__name__} for task "
            f"'{request.task}'.",
            hint=last_error[:800],
        )

    # -- prompt helpers -------------------------------------------------------- #

    @staticmethod
    def schema_instruction(model_cls: type[BaseModel]) -> str:
        schema = json.dumps(model_cls.model_json_schema(), indent=2)
        return (
            "Respond with a single JSON object and nothing else — no prose, no code "
            "fences, no trailing commentary. It must validate against this JSON Schema:\n\n"
            f"{schema}"
        )
