"""Suite-local fixtures for output-schema-validation tests (TASK.md §4).

Self-contained (no DB, no Redis, no live server) — mirrors the established
CompletionUseCase integration-fake pattern (tests/vector_cache/conftest.py,
tests/cache_alias_billing/conftest.py). Exercises CompletionUseCase.complete()
directly through the no-model_router fallback branch (`upstream.complete(body)`,
M5's `else` leg) — the model_router branch is already covered by the
model_fallbacks suite and is not this task's own contract surface to duplicate.

SequencedFakeUpstream is REUSED verbatim from tests.model_fallbacks.conftest
(records every submitted payload snapshot; replays a scripted (status, body)
or exception sequence) — exactly what the bounded-retry scenarios need.
"""

from __future__ import annotations

import uuid
from typing import Any

from gateway.keys.domain.entities import AuthzResult
from gateway.proxy.domain.entities import GuardrailResult

from tests.model_fallbacks.conftest import SequencedFakeUpstream  # noqa: F401

# --- identities ---------------------------------------------------------
TENANT_A = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
KEY_ID = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")

MODEL = "gpt-test"

# --- a well-formed JSON Schema + matching/mismatching content -----------
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}

# Structurally invalid AT THE META-SCHEMA LEVEL (type must be a string or array
# of strings, never an int) — check_schema_well_formed must reject this without
# ever touching the network (M3).
MALFORMED_SCHEMA = {"type": 123}

VALID_CONTENT = '{"answer": "yes"}'
MISMATCHED_CONTENT = '{"wrong_field": "nope"}'  # valid JSON, fails schema (missing "answer")
UNPARSEABLE_CONTENT = "not json at all {"  # not valid JSON at all


def response_format_json_schema(schema: dict[str, Any] = SCHEMA) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": "answer_schema", "schema": schema}}


def make_body(
    *,
    validate_output: bool | None = True,
    response_format: dict[str, Any] | None = None,
    stream: bool = False,
    model: str = MODEL,
) -> dict[str, Any]:
    """Build a chat-completions request body for the output-validation suite.

    response_format=None -> DEFAULT to a valid json_schema directive (the common
    case every "engaged" scenario needs); pass {} explicitly to OMIT the key
    entirely (the "requires json_schema" reject scenario).
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
    }
    if response_format is None:
        body["response_format"] = response_format_json_schema()
    elif response_format:
        body["response_format"] = response_format
    if validate_output is not None:
        body["validate_output"] = validate_output
    if stream:
        body["stream"] = True
    return body


def make_upstream_body(
    content: str,
    *,
    resp_id: str = "resp-1",
    model: str = MODEL,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": resp_id,
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": usage or {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


def make_multi_choice_body(
    contents: list[str],
    *,
    resp_id: str = "resp-multi",
    model: str = MODEL,
) -> dict[str, Any]:
    return {
        "id": resp_id,
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": c}} for c in contents],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


# --- CompletionUseCase integration fakes ---------------------------------
class FakeAuthenticator:
    def __init__(
        self,
        *,
        tenant_id: uuid.UUID = TENANT_A,
        cache_enabled: bool = False,
        guardrail_configs: dict[str, Any] | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._cache_enabled = cache_enabled
        self._guardrail_configs = guardrail_configs or {}

    async def authenticate(self, raw_key: str) -> AuthzResult:
        return AuthzResult(
            tenant_id=self._tenant_id,
            key_id=KEY_ID,
            cache_enabled=self._cache_enabled,
            guardrail_configs=self._guardrail_configs,
        )


class FakeModelChecker:
    async def is_active(self, model_id: str) -> bool:
        return True


class FakeUsageRecorder:
    """Records every record() call; declares the full extras seam (recorder.py parity)."""

    supported_extras = frozenset(
        {
            "team_id",
            "cached",
            "guardrail_blocked",
            "blocked_by",
            "pii_masked",
            "pricing_unit",
            "quantity",
            "usage_source",
            "provider_generation_id",
            "disconnect_estimate",
        }
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))

    @property
    def call_count(self) -> int:
        return len(self.calls)


class FakeResponseCache:
    """Exact-match cache stub with real in-memory dict semantics (no semantic layer)."""

    def __init__(self, exact: dict[str, dict[str, Any]] | None = None) -> None:
        self.store: dict[str, dict[str, Any]] = dict(exact or {})
        self.get_calls: list[str] = []
        self.set_calls: list[str] = []

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        self.get_calls.append(cache_key)
        return self.store.get(cache_key)

    async def set(self, cache_key: str, body: dict[str, Any], ttl_seconds: int) -> None:
        self.set_calls.append(cache_key)
        self.store[cache_key] = body


class FakeGuardrailEvaluator:
    """evaluate_pre never blocks/masks; evaluate_post prefixes content (proves ordering, M10)."""

    def __init__(self) -> None:
        self.evaluate_post_calls: int = 0

    async def evaluate_pre(
        self, messages: list[dict[str, Any]], guardrail_configs: dict[str, Any]
    ) -> GuardrailResult:
        return GuardrailResult(blocked=False, blocked_by=None, masked_messages=None, events=[])

    async def evaluate_post(
        self, response_body: dict[str, Any], guardrail_configs: dict[str, Any]
    ) -> dict[str, Any]:
        self.evaluate_post_calls += 1
        masked = dict(response_body)
        masked["choices"] = [
            {
                "message": {
                    "role": "assistant",
                    "content": "MASKED:" + (c.get("message") or {}).get("content", ""),
                }
            }
            for c in response_body.get("choices", [])
        ]
        return masked
