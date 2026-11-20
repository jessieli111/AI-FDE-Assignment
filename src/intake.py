"""Harbourline intake: one support email in, one validated Intake out.

How it works:
  1. The prompt is loaded from prompts/intake_v1.md (the only prompt).
  2. The model gets one tool, record_intake, whose input schema is Intake.
  3. The tool input is validated here, in code, with Pydantic.
  4. If it is invalid, the error text goes back to the model as the tool's
     result and the model tries again.
  5. The loop stops at the first valid object or after MAX_CALLS calls.
     Every call is logged with its outcome, tokens and latency.

Config comes from the environment (or .env):
  LLM_PROVIDER  anthropic (default) or openai_compatible
  LLM_MODEL     model name; defaults to claude-haiku-5-5 for anthropic
  MAX_TOKENS    per call (default 1024)
  anthropic:          ANTHROPIC_API_KEY
  openai_compatible:  LLM_BASE_URL and LLM_API_KEY (e.g. Gemini, Groq, OpenRouter)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "intake_v1.md"
PROMPT_VERSION = PROMPT_PATH.stem  # "intake_v1"
MAX_CALLS = 3
TOOL_NAME = "record_intake"
TOOL_DESCRIPTION = "Record the structured intake for this customer email. Call it exactly once."

log = logging.getLogger("intake")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

Category = Literal["coverage", "billing", "claim", "other"]
NextAction = Literal["ask", "draft", "escalate"]
MissingField = Literal[
    "policy_number",
    "claim_id",
    "member_name",
    "date_of_birth",
    "service_date",
    "provider_name",
    "amount",
]


class Intake(BaseModel):
    """The structured result for one customer email."""

    model_config = ConfigDict(extra="forbid")

    category: Category = Field(description="What the customer's request is about.")
    missing_fields: list[MissingField] = Field(
        description="Fields the agent needs from the customer before acting. Empty if none."
    )
    next_action: NextAction = Field(
        description="ask = request missing_fields; draft = reply can be drafted now; escalate = needs a senior agent."
    )
    customer_message: str = Field(
        min_length=1,
        description="Message for the agent to review and send. Never sent automatically.",
    )
    confidence: float = Field(ge=0, le=1, description="0 to 1: how sure you are of category and next_action.")

    @model_validator(mode="after")
    def check_action_matches_missing_fields(self) -> "Intake":
        # ask = there is something to ask for; draft = nothing is missing.
        # escalate may have missing fields: a senior agent handles the email either way.
        if self.next_action == "ask" and not self.missing_fields:
            raise ValueError(
                "next_action is 'ask' but missing_fields is empty. List the fields to ask for, "
                "or use 'draft' if nothing is missing."
            )
        if self.next_action == "draft" and self.missing_fields:
            raise ValueError(
                f"next_action is 'draft' but missing_fields is {self.missing_fields}. "
                "Use 'ask' to request them, or empty missing_fields if nothing is needed."
            )
        return self


# ---------------------------------------------------------------------------
# Config and prompt
# ---------------------------------------------------------------------------


def load_env(path: Path = ROOT / ".env") -> None:
    """Read KEY=value lines from .env. Variables already set in the environment win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    provider: str
    model: str
    max_tokens: int
    base_url: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        provider = os.getenv("LLM_PROVIDER", "anthropic")
        model = os.getenv("LLM_MODEL") or ("claude-haiku-5-5" if provider == "anthropic" else "")
        if not model:
            raise ValueError("Set LLM_MODEL in .env for this provider.")
        return cls(
            provider=provider,
            model=model,
            max_tokens=int(os.getenv("MAX_TOKENS", "1024")),
            base_url=os.getenv("LLM_BASE_URL") or None,
        )


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def tool_schema() -> dict:
    return Intake.model_json_schema()


# ---------------------------------------------------------------------------
# Backends: one per API format. Each knows how to make a call and how to
# send a validation error back. The loop below doesn't care which it has.
# ---------------------------------------------------------------------------


@dataclass
class Reply:
    tool_id: str | None
    tool_input: Any          # parsed tool arguments, or None
    input_tokens: int
    output_tokens: int
    stop_reason: str | None
    message: Any = None      # the assistant message, for the no-tool-call case
    parse_error: str | None = None


class AnthropicBackend:
    def __init__(self, config: Config, client: Any = None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client, self.config = client, config
        self.tool = {"name": TOOL_NAME, "description": TOOL_DESCRIPTION, "input_schema": tool_schema()}

    def start(self, system: str, email: str) -> list[dict]:
        self.system = system
        return [{"role": "user", "content": email}]

    def call(self, messages: list[dict]) -> Reply:
        r = self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            system=self.system,
            tools=[self.tool],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=messages,
        )
        block = next((b for b in r.content if b.type == "tool_use"), None)
        return Reply(
            tool_id=block.id if block else None,
            tool_input=block.input if block else None,
            input_tokens=r.usage.input_tokens,
            output_tokens=r.usage.output_tokens,
            stop_reason=r.stop_reason,
            message=r.content,
        )

    def send_error(self, messages: list[dict], reply: Reply, raw: Any, error: str) -> None:
        if reply.tool_id is None:
            messages.append({"role": "assistant", "content": reply.message})
            messages.append({"role": "user", "content": f"{error}\nCall {TOOL_NAME} now."})
            return
        messages.append(
            {"role": "assistant", "content": [{"type": "tool_use", "id": reply.tool_id, "name": TOOL_NAME, "input": raw}]}
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": reply.tool_id,
                        "is_error": True,
                        "content": f"Validation failed:\n{error}\nCall {TOOL_NAME} again with a corrected object.",
                    }
                ],
            }
        )


class OpenAICompatibleBackend:
    """Chat Completions format: Gemini, Groq, OpenRouter, Ollama and others."""

    def __init__(self, config: Config, client: Any = None):
        if client is None:
            import openai

            if not config.base_url:
                raise ValueError("Set LLM_BASE_URL in .env for the openai_compatible provider.")
            client = openai.OpenAI(base_url=config.base_url, api_key=os.getenv("LLM_API_KEY"))
        self.client, self.config = client, config
        self.tool = {
            "type": "function",
            "function": {"name": TOOL_NAME, "description": TOOL_DESCRIPTION, "parameters": _plain_schema(tool_schema())},
        }

    def start(self, system: str, email: str) -> list[dict]:
        return [{"role": "system", "content": system}, {"role": "user", "content": email}]

    def call(self, messages: list[dict]) -> Reply:
        r = self.client.chat.completions.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            tools=[self.tool],
            tool_choice="required",
            messages=messages,
        )
        choice = r.choices[0]
        calls = choice.message.tool_calls or []
        usage = r.usage
        reply = Reply(
            tool_id=None,
            tool_input=None,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            stop_reason=choice.finish_reason,
            # Keep the whole message: Gemini 3 attaches a thought signature to each
            # tool call and rejects the next request (400) if it isn't sent back.
            message=_as_dict(choice.message),
        )
        if calls:
            reply.tool_id = calls[0].id or "call_1"
            args = calls[0].function.arguments
            try:
                reply.tool_input = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError as exc:
                reply.tool_input = args
                reply.parse_error = f"- (arguments): not valid JSON ({exc.msg})"
        return reply

    def send_error(self, messages: list[dict], reply: Reply, raw: Any, error: str) -> None:
        if reply.tool_id is None:
            messages.append({"role": "assistant", "content": (reply.message or {}).get("content") or ""})
            messages.append({"role": "user", "content": f"{error}\nCall {TOOL_NAME} now."})
            return
        # Send the assistant message back as received, with only the first tool call's
        # arguments replaced by what we validated (differs when a failure was injected).
        arguments = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        assistant = dict(reply.message or {"role": "assistant"})
        calls = [dict(c) for c in assistant.get("tool_calls") or []]
        if calls:
            calls[0]["id"] = reply.tool_id
            calls[0]["function"] = {**calls[0].get("function", {}), "name": TOOL_NAME, "arguments": arguments}
        else:
            calls = [{"id": reply.tool_id, "type": "function", "function": {"name": TOOL_NAME, "arguments": arguments}}]
        assistant.update(role="assistant", tool_calls=calls)
        assistant.setdefault("content", None)
        messages.append(assistant)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": reply.tool_id,
                "content": f"Validation failed:\n{error}\nCall {TOOL_NAME} again with a corrected object.",
            }
        )


def _as_dict(message: Any) -> dict:
    """SDK message object -> plain dict, keeping provider-specific extra fields."""
    if isinstance(message, dict):
        return message
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    return {"role": "assistant", "content": getattr(message, "content", None)}


def _plain_schema(schema: dict) -> dict:
    """Drop JSON-schema keys some providers reject. Pydantic still enforces them in code."""
    if isinstance(schema, dict):
        return {k: _plain_schema(v) for k, v in schema.items() if k not in ("title", "additionalProperties")}
    if isinstance(schema, list):
        return [_plain_schema(v) for v in schema]
    return schema


BACKENDS = {"anthropic": AnthropicBackend, "openai_compatible": OpenAICompatibleBackend}

# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


@dataclass
class Attempt:
    call: int
    outcome: Literal["valid", "invalid"]
    input_tokens: int
    output_tokens: int
    latency_ms: int
    error: str | None = None
    raw: Any = None  # the tool input exactly as the model sent it
    injected: bool = False


@dataclass
class IntakeRun:
    intake: Intake | None
    provider: str
    model: str
    prompt_version: str = PROMPT_VERSION
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        return "valid" if self.intake is not None else "failed"

    @property
    def input_tokens(self) -> int:
        return sum(a.input_tokens for a in self.attempts)

    @property
    def output_tokens(self) -> int:
        return sum(a.output_tokens for a in self.attempts)

    @property
    def latency_ms(self) -> int:
        return sum(a.latency_ms for a in self.attempts)


class IntakeFailed(RuntimeError):
    def __init__(self, result: IntakeRun):
        self.result = result
        last = result.attempts[-1].error if result.attempts else "no calls made"
        super().__init__(f"No valid Intake after {len(result.attempts)} calls. Last error: {last}")


def format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors(include_url=False):
        where = ".".join(str(p) for p in err["loc"]) or "(object)"
        lines.append(f"- {where}: {err['msg']}")
    return "\n".join(lines)


def _corrupt(raw: Any) -> Any:
    """Break a valid object on purpose, to demonstrate the retry path (--inject-failure)."""
    if not isinstance(raw, dict):
        return raw
    bad = dict(raw)
    bad["category"] = "claims"  # not an allowed value
    bad["confidence"] = 1.7     # out of range
    return bad


def run_traced(
    email: str,
    *,
    client: Any = None,
    config: Config | None = None,
    inject_failure: bool = False,
) -> IntakeRun:
    """Run the loop and return the result plus every attempt, valid or not."""
    config = config or Config.from_env()
    backend = BACKENDS[config.provider](config, client)
    messages = backend.start(load_prompt(), email)
    result = IntakeRun(intake=None, provider=config.provider, model=config.model)

    for call in range(1, MAX_CALLS + 1):
        start = time.perf_counter()
        reply = backend.call(messages)
        latency_ms = int((time.perf_counter() - start) * 1000)

        raw, injected = reply.tool_input, False
        if reply.tool_id is None:
            error = f"- (response): no {TOOL_NAME} call (stop_reason={reply.stop_reason})"
        elif reply.parse_error:
            error = reply.parse_error
        else:
            injected = inject_failure and call == 1
            if injected:
                raw = _corrupt(raw)
            try:
                result.intake = Intake.model_validate(raw)
                error = None
            except ValidationError as exc:
                error = format_validation_error(exc)

        result.attempts.append(
            Attempt(
                call=call,
                outcome="valid" if error is None else "invalid",
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                latency_ms=latency_ms,
                error=error,
                raw=raw,
                injected=injected,
            )
        )

        if error is None:
            log.info("call %d: valid (%d ms)", call, latency_ms)
            return result

        log.warning("call %d: invalid%s\n%s", call, " (injected)" if injected else "", error)
        if call == MAX_CALLS:
            break
        backend.send_error(messages, reply, raw, error)
        log.info("call %d: error text sent back to the model", call)

    log.error("stopped after %d calls without a valid object", len(result.attempts))
    return result


def run(email: str, **kwargs: Any) -> Intake:
    """One email in, one validated Intake out. Raises IntakeFailed after MAX_CALLS invalid calls."""
    result = run_traced(email, **kwargs)
    if result.intake is None:
        raise IntakeFailed(result)
    return result.intake
