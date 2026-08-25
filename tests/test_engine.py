"""Engine contract: JSON extraction, schema validation and repair."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from orchestrator.core.errors import StructuredOutputError
from orchestrator.engine.base import Engine, EngineRequest, EngineResponse, extract_json
from orchestrator.engine.mock import MockEngine


class Tiny(BaseModel):
    name: str
    count: int


class ScriptedEngine(Engine):
    name = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.seen: list[EngineRequest] = []

    def complete(self, request: EngineRequest) -> EngineResponse:
        self.seen.append(request)
        return EngineResponse(text=self.replies.pop(0))


def test_extract_json_bare():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert extract_json('here you go:\n```json\n{"a": 2}\n```\nthanks') == {"a": 2}


def test_extract_json_embedded_in_prose():
    assert extract_json('Sure. {"a": 3} Let me know.') == {"a": 3}


def test_extract_json_returns_none_for_garbage():
    assert extract_json("no json here") is None


def test_generate_structured_validates():
    engine = ScriptedEngine(['{"name": "x", "count": 2}'])
    model, _ = engine.generate_structured(EngineRequest(task="t", prompt="p"), Tiny)
    assert model.count == 2


def test_generate_structured_repairs_once():
    engine = ScriptedEngine(['{"name": "x"}', '{"name": "x", "count": 7}'])
    model, _ = engine.generate_structured(EngineRequest(task="t", prompt="p"), Tiny)
    assert model.count == 7
    assert "did not satisfy the required JSON schema" in engine.seen[1].prompt


def test_generate_structured_gives_up_with_a_typed_error():
    engine = ScriptedEngine(["nope", "still nope"])
    with pytest.raises(StructuredOutputError):
        engine.generate_structured(EngineRequest(task="t", prompt="p"), Tiny)


def test_mock_engine_is_deterministic():
    engine = MockEngine()
    request = EngineRequest(
        task="requirements",
        prompt="",
        metadata={"issue": {"key": "MOB-1", "summary": "s", "acceptance_criteria": ["a", "b"]}},
    )
    first = engine.complete(request).structured
    second = engine.complete(request).structured
    assert first == second
    assert len(first["requirements"]) == 2


def test_mock_engine_can_be_told_to_fail():
    from orchestrator.core.errors import EngineError

    engine = MockEngine(fail_task="plan")
    with pytest.raises(EngineError):
        engine.complete(EngineRequest(task="plan", prompt=""))
