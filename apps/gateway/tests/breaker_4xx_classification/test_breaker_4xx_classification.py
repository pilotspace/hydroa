"""Failing-first (RED) suite — a client error must never trip a circuit breaker.

breaker-4xx-classification PLAN.md §4. Closes todo #60, including the two sites it does
not name.

RED reason expected (before Build): `status_counts_as_upstream_failure` does not exist in
`gateway.proxy.infrastructure.circuit_breaker`, so every test here fails at import.

The defect: a tenant revokes or rotates a BYOK key, the provider answers 401, and the
call sites below feed that 401 to `breaker.on_upstream_error()`. Five of them and the
tenant's OWN breaker opens — so their CORRECTED traffic then 502s for the cooldown.

The rule already exists in this codebase. `RetryPolicy.classify_status` in
`proxy/infrastructure/upstream_retry.py` says 429/408/5xx count and every other 4xx is a
terminal-but-successful round trip, and every provider routed through `execute_with_retry`
already behaves that way. The four hand-rolled `except httpx.HTTPStatusError ->
on_upstream_error()` sites simply bypass it. So this suite gates ONE rule shared, not a
new rule invented.

Tin's freeze decision (2026-08-07): a terminal 4xx records SUCCESS, matching the existing
seam, rather than introducing a third breaker verb.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.upstream_retry import DEFAULT_RETRY_POLICY

TENANT = uuid.uuid4()

# Deliberately ABOVE the breaker's _FAILURE_THRESHOLD of 5. If a future change raises the
# threshold, a 5-call test would start passing for the wrong reason; 10 keeps failing.
CALLS = 10


def _responder(status: int, body: dict[str, Any] | None = None) -> Callable[..., Any]:
    """A MockTransport handler that always answers `status`, counting requests."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json=body if body is not None else {"error": "nope"})

    handler.calls = calls  # type: ignore[attr-defined]
    return handler


@pytest.fixture
def stub_http(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Callable[..., Any]], None]]:
    """Force every `httpx.AsyncClient()` built inside a client to use MockTransport.

    These adapters construct their own `httpx.AsyncClient` inline (`async with
    httpx.AsyncClient(timeout=...)`), so there is no transport seam to inject. Patching the
    class is the least invasive way to exercise the REAL method bodies — which is the whole
    point: the defect lives in those bodies' except-clauses, so a test double that overrides
    the method would prove nothing.
    """

    def install(handler: Callable[..., Any]) -> None:
        real = httpx.AsyncClient

        class _Stubbed(real):  # type: ignore[misc,valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs["transport"] = httpx.MockTransport(handler)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _Stubbed)

    yield install


# ─────────────────────────────────────────────────────────────────────────────
# ONE RULE — the predicate, and the proof it never drifts from RetryPolicy
# ─────────────────────────────────────────────────────────────────────────────


def test_predicate_counts_only_408_429_and_5xx() -> None:
    """Only real upstream trouble counts. covers: M1, M2, R"""
    from gateway.proxy.infrastructure.circuit_breaker import status_counts_as_upstream_failure

    for status in (400, 401, 403, 404, 409, 422, 200, 204, 301):
        assert not status_counts_as_upstream_failure(status), (
            f"{status} is a client error or a success — it must not count toward the breaker"
        )
    for status in (408, 429, 500, 502, 503):
        assert status_counts_as_upstream_failure(status), (
            f"{status} is upstream trouble — it MUST still count, or this task has made the "
            "breaker worse at its actual job"
        )


def test_predicate_agrees_with_retry_policy_for_every_status() -> None:
    """The anti-drift test: one rule, two shapes, over the WHOLE domain. covers: M4

    Sampled points would let the two definitions diverge in the gap between samples, and a
    silently-diverged breaker policy is exactly the bug being fixed. So walk 100..599.
    """
    from gateway.proxy.infrastructure.circuit_breaker import status_counts_as_upstream_failure

    for status in range(100, 600):
        expected = DEFAULT_RETRY_POLICY.classify_status(status) is not None
        assert status_counts_as_upstream_failure(status) is expected, (
            f"status {status}: predicate says {status_counts_as_upstream_failure(status)}, "
            f"RetryPolicy says {expected}. These must never disagree — two rules is the "
            "defect, not the fix."
        )


# ─────────────────────────────────────────────────────────────────────────────
# PER SITE — six offenders, gated separately so one can be dropped without
# unpicking the others
# ─────────────────────────────────────────────────────────────────────────────


async def test_finetune_submit_401_does_not_open_the_breaker(
    stub_http: Callable[[Callable[..., Any]], None],
) -> None:
    """A revoked BYOK key must not lock the tenant out. covers: M1"""
    from gateway.finetune.infrastructure.openai_client import OpenAIFinetuneClient

    stub_http(_responder(401))
    client = OpenAIFinetuneClient()

    for _ in range(CALLS):
        with pytest.raises(httpx.HTTPStatusError):
            await client.submit(TENANT, _credential(), {"model": "gpt-4o-mini"})

    breaker = client._breaker_for(TENANT)  # noqa: SLF001 — asserting on breaker state IS the test
    assert breaker.call_allowed(), (
        f"{CALLS} consecutive 401s opened the tenant's own breaker. Their corrected traffic "
        "would now 502 for the cooldown — this is todo #60's live availability bug."
    )


async def test_finetune_poll_404_costs_one_request(
    stub_http: Callable[[Callable[..., Any]], None],
) -> None:
    """A 4xx is terminal, so it must not burn the retry budget. covers: M3"""
    from gateway.finetune.infrastructure.openai_client import OpenAIFinetuneClient

    handler = _responder(404)
    stub_http(handler)
    client = OpenAIFinetuneClient()

    with pytest.raises(httpx.HTTPStatusError):
        await client.poll(TENANT, _credential(), "job-does-not-exist")

    assert len(handler.calls) == 1, (  # type: ignore[attr-defined]
        f"poll made {len(handler.calls)} requests for a 404. "  # type: ignore[attr-defined]
        "A missing job will never appear on retry — retrying it wastes two round trips per "
        "poll cycle, forever."
    )


async def test_finetune_cancel_409_does_not_open_the_breaker(
    stub_http: Callable[[Callable[..., Any]], None],
) -> None:
    """Cancelling an already-finished job is a client error. covers: M1"""
    from gateway.finetune.infrastructure.openai_client import OpenAIFinetuneClient

    stub_http(_responder(409))
    client = OpenAIFinetuneClient()

    for _ in range(CALLS):
        with pytest.raises(httpx.HTTPStatusError):
            await client.cancel(TENANT, _credential(), "job-already-done")

    assert client._breaker_for(TENANT).call_allowed(), (  # noqa: SLF001
        "repeated 409s opened the breaker"
    )


async def test_embeddings_401_does_not_open_the_breaker_and_costs_one_call(
    stub_http: Callable[[Callable[..., Any]], None],
) -> None:
    """Vector-store embeddings: same defect, same tenant lockout. covers: M1, M3"""
    from gateway.proxy.domain.errors import UpstreamUnavailableError
    from gateway.vector_stores.infrastructure.embedding_client import VectorStoreEmbeddingClient

    handler = _responder(401)
    stub_http(handler)
    client = VectorStoreEmbeddingClient(api_key="revoked")

    with pytest.raises(UpstreamUnavailableError):
        await client.embed(TENANT, "text-embedding-3-small", ["hello"])

    assert len(handler.calls) == 1, (  # type: ignore[attr-defined]
        "the retry loop spent its second attempt on a 401 — a revoked key is not going to "
        "become valid between attempts"
    )
    assert client._breaker_for(TENANT).call_allowed(), (  # noqa: SLF001
        "a single 401 batch must not move the breaker toward open"
    )


# The two Bedrock sites are NOT in todo #60 — I found them by reading every
# `status_code >= 400` in the tree. `git log -L` on both showed neither was a deliberate
# breaker decision: bedrock_embeddings' `>= 400` is documented as fail-fast for the Titan
# N-call FAN-OUT (on_upstream_error rode along inside the same `if`), and the streaming one
# claims to "mirror AnthropicCompletionUpstream" — which uses `>= 500`.


async def test_bedrock_embeddings_400_does_not_open_the_breaker() -> None:
    """A malformed Titan request must not trip the breaker. covers: M1"""
    from gateway.proxy.domain.provider_credentials import BedrockCredential
    from gateway.proxy.infrastructure.bedrock_embeddings import BedrockEmbeddingsProvider
    from gateway.proxy.domain.credential_context import (
        reset_provider_credential,
        set_provider_credential,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "ValidationException"})

    provider = BedrockEmbeddingsProvider(  # type: ignore[call-arg]
        endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com",
    )
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # noqa: SLF001
    cred = BedrockCredential(
        access_key_id="AKIDTEST000000000000",
        secret_access_key="fakesecretkey0000000000000000000000000000",
        region="us-east-1",
    )

    tok = set_provider_credential(cred)
    try:
        for _ in range(CALLS):
            await provider.post_json(
                "/embeddings", {"model": "amazon.titan-embed-text-v1", "input": "hello"}
            )
    finally:
        reset_provider_credential(tok)
        await provider._client.aclose()  # noqa: SLF001

    assert provider._breaker.call_allowed(), (  # noqa: SLF001
        f"{CALLS} consecutive 400s opened the Bedrock embeddings breaker. A caller sending a "
        "bad request would take the provider down for every other caller on this instance."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PROTECTION NOT WEAKENED — the regression direction
# ─────────────────────────────────────────────────────────────────────────────


async def test_finetune_submit_500_still_opens_the_breaker(
    stub_http: Callable[[Callable[..., Any]], None],
) -> None:
    """A real outage must still trip. covers: M2

    Without this arm, "stop counting 4xx" could be implemented as "stop counting anything"
    and every test above would still pass.
    """
    from gateway.proxy.domain.errors import CircuitOpenError
    from gateway.finetune.infrastructure.openai_client import OpenAIFinetuneClient

    stub_http(_responder(503))
    client = OpenAIFinetuneClient()

    opened = False
    for _ in range(CALLS):
        try:
            await client.submit(TENANT, _credential(), {"model": "gpt-4o-mini"})
        except CircuitOpenError:
            opened = True
            break
        except httpx.HTTPStatusError:
            continue

    assert opened, "five consecutive 503s must open the breaker — this is its whole job"


async def test_429_still_counts_toward_the_breaker() -> None:
    """Upstream backpressure is not a client mistake. covers: R"""
    from gateway.proxy.infrastructure.circuit_breaker import status_counts_as_upstream_failure

    assert status_counts_as_upstream_failure(429), (
        "429 is the provider shedding our load — the one 4xx that must keep counting, or we "
        "lose the only signal that we are being rate limited"
    )
    assert status_counts_as_upstream_failure(408)


def test_a_transport_error_is_unaffected_by_this_change() -> None:
    """Transport failures were never status-classified and must stay counted. covers: M2"""
    breaker = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        breaker.on_upstream_error()
    assert not breaker.call_allowed(), (
        "the breaker's transport path is untouched by this task; if this fails, something "
        "far more basic broke"
    )


def _credential() -> Any:
    """A minimal bearer credential — the clients only read `.secret`."""
    from gateway.proxy.domain.provider_credentials import BearerCredential
    from pydantic import SecretStr

    return BearerCredential(secret=SecretStr("sk-test-not-a-real-key"))
