"""Offline tests with a fake client: no API key needed.  pytest -q"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.intake import MAX_CALLS, PROMPT_PATH, Config, Intake, IntakeFailed, run, run_traced  # noqa: E402

CONFIG = Config(provider="anthropic", model="fake-model", max_tokens=256)
OPENAI_CONFIG = Config(provider="openai_compatible", model="fake-model", max_tokens=256, base_url="http://fake")

VALID = {
    "category": "coverage",
    "missing_fields": ["policy_number"],
    "next_action": "ask",
    "customer_message": "Could you send your policy number? Harbourline Support",
    "confidence": 0.9,
}
INVALID = {**VALID, "category": "claims", "confidence": 1.7}


class FakeClient:
    """Returns the queued tool inputs in order and records every request."""

    def __init__(self, *inputs):
        self.inputs = list(inputs)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        block = SimpleNamespace(type="tool_use", id=f"tool_{len(self.requests)}", input=self.inputs.pop(0))
        return SimpleNamespace(
            content=[block],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


class FakeOpenAIClient:
    """Chat Completions shape: arguments arrive as a JSON string."""

    def __init__(self, *inputs):
        self.inputs = list(inputs)
        self.requests = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        args = self.inputs.pop(0)
        call = SimpleNamespace(
            id=f"call_{len(self.requests)}",
            function=SimpleNamespace(name="record_intake", arguments=args if isinstance(args, str) else json.dumps(args)),
        )
        message = SimpleNamespace(content=None, tool_calls=[call])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
            usage=SimpleNamespace(prompt_tokens=80, completion_tokens=30),
        )


def test_valid_first_call_stops_after_one():
    client = FakeClient(VALID)
    result = run_traced("email", client=client, config=CONFIG)
    assert result.outcome == "valid"
    assert len(client.requests) == 1
    assert result.attempts[0].input_tokens == 100


def test_validation_error_goes_back_to_model_then_succeeds():
    client = FakeClient(INVALID, VALID)
    result = run_traced("email", client=client, config=CONFIG)
    assert [a.outcome for a in result.attempts] == ["invalid", "valid"]
    assert result.attempts[0].raw == INVALID  # raw output kept, not dropped
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["is_error"] is True
    assert "category" in tool_result["content"] and "confidence" in tool_result["content"]


def test_loop_stops_at_three_calls():
    client = FakeClient(*[INVALID] * 5)
    result = run_traced("email", client=client, config=CONFIG)
    assert len(client.requests) == MAX_CALLS == 3
    assert result.outcome == "failed"
    with pytest.raises(IntakeFailed):
        run("email", client=FakeClient(*[INVALID] * 3), config=CONFIG)


def test_inject_failure_forces_one_retry():
    client = FakeClient(VALID, VALID)
    result = run_traced("email", client=client, config=CONFIG, inject_failure=True)
    assert [a.outcome for a in result.attempts] == ["invalid", "valid"]
    assert result.attempts[0].injected is True


def test_openai_compatible_retry_sends_tool_message():
    client = FakeOpenAIClient(INVALID, VALID)
    result = run_traced("email", client=client, config=OPENAI_CONFIG)
    assert [a.outcome for a in result.attempts] == ["invalid", "valid"]
    assert result.attempts[1].input_tokens == 80
    sent = client.requests[1]["messages"]
    assert sent[0]["role"] == "system" and sent[0]["content"] == PROMPT_PATH.read_text(encoding="utf-8")
    assert sent[-1]["role"] == "tool" and "Validation failed" in sent[-1]["content"]


def test_openai_compatible_bad_json_is_logged_and_retried():
    client = FakeOpenAIClient('{"category": "coverage",', VALID)
    result = run_traced("email", client=client, config=OPENAI_CONFIG)
    assert result.attempts[0].outcome == "invalid" and "not valid JSON" in result.attempts[0].error
    assert result.outcome == "valid"


def test_prompt_loaded_from_file_and_config_from_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "model-from-env")
    monkeypatch.setenv("MAX_TOKENS", "321")
    client = FakeClient(VALID)
    run_traced("email", client=client)
    req = client.requests[0]
    assert req["system"] == PROMPT_PATH.read_text(encoding="utf-8")
    assert req["model"] == "model-from-env" and req["max_tokens"] == 321


def test_ask_requires_missing_fields():
    with pytest.raises(ValidationError):
        Intake.model_validate({**VALID, "missing_fields": []})


def test_draft_with_nothing_missing_is_valid():
    Intake.model_validate({**VALID, "next_action": "draft", "missing_fields": []})
