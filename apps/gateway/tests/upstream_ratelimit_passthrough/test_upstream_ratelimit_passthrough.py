"""RED suite — upstream 429 rate-limit passthrough (TASK.md §2 / §4).

11 tests, one per §4 row.  ALL must run RED until BUILD creates:
  - UpstreamRateLimitedError          (domain/errors.py)
  - UPSTREAM_RATE_LIMITED ErrorSpec   (core/error_catalog.py)
  - execute_with_retry 429-exhaust → UpstreamRateLimitedError (infrastructure/upstream_retry.py)
  - FallbackModelRouter.complete() tracks max retry_after on RL exhaustion (application/fallback_router.py)
  - CompletionUseCase non-stream + pre-first-byte stream maps UpstreamRateLimitedError → 429 (use_cases.py)

Construction patterns mirror tests/retry_policy/ (execute_with_retry),
tests/model_fallbacks_wiring/ (FallbackModelRouter), tests/streaming_resilience/
(open_resilient_stream + FakeUsageRecorder), and tests/proxy/test_proxy_completions.py
(end-to-end DB-backed use-case tests).

asyncio_mode = "auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Domain error import — this is the RED line: UpstreamRateLimitedError must NOT
# exist yet.  Import UpstreamUnavailableError (existing) so the assertion
# "isinstance(…, UpstreamUnavailableError)" is resolvable at collection time.
# ---------------------------------------------------------------------------
from gateway.proxy.domain.errors import UpstreamUnavailableError

# Re-used from existing test harnesses
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.upstream_retry import (
    execute_with_retry,
    parse_retry_after,
)
from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.streaming_resilience import open_resilient_stream


# ===========================================================================
# Helpers shared across groups
# ===========================================================================


class _CountingBreaker(CircuitBreaker):
    """Circuit breaker that records call counts (mirrors retry_policy/conftest.py)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.error_call_count: int = 0
        self.success_call_count: int = 0

    def on_upstream_error(self) -> None:
        self.error_call_count += 1
        super().on_upstream_error()

    def record_success(self) -> None:
        self.success_call_count += 1
        super().record_success()


def _make_json_response(
    status: int,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build a minimal httpx.Response with JSON body and optional extra headers."""
    content = json.dumps(body or {}).encode()
    resp_headers: dict[str, str] = {"content-type": "application/json"}
    if headers:
        resp_headers.update(headers)
    return httpx.Response(
        status_code=status,
        headers=resp_headers,
        content=content,
    )


def _seq_do_request(
    items: list[Any],
) -> Any:
    """Return a do_request callable that replays items (Response or Exception) in order.

    Mirrors the _seq_do_request helper in tests/retry_policy/test_upstream_retry.py.
    """
    state: dict[str, int] = {"i": 0}

    async def _do() -> httpx.Response:
        i = state["i"]
        state["i"] += 1
        entry = items[i]
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, type) and issubclass(entry, BaseException):
            raise entry("mock transport error")
        return entry  # type: ignore[return-value]

    return _do


def _render(resp: httpx.Response) -> tuple[int, dict[str, Any]]:
    return resp.status_code, resp.json()


# ===========================================================================
# GROUP A — execute_with_retry: 429 surfaces as UpstreamRateLimitedError
# Tests: RP-1, RP-2, RP-3
# ===========================================================================


async def test_retry_429_exhaust_raises_ratelimit_with_retry_after() -> None:
    """RP-1: exhausting retries on a 429+Retry-After:7 → UpstreamRateLimitedError(.retry_after==7.0).

    Also asserts isinstance(UpstreamUnavailableError) to lock the IS-A invariant.
    """
    # Import the new symbol — ImportError here = RED for the right reason.
    from gateway.proxy.domain.errors import UpstreamRateLimitedError  # noqa: PLC0415

    do_request = _seq_do_request(
        [
            _make_json_response(429, {"error": "too many requests"}, {"Retry-After": "7"}),
            _make_json_response(429, {"error": "too many requests"}, {"Retry-After": "7"}),
        ]
    )
    breaker = _CountingBreaker()
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(UpstreamRateLimitedError) as exc_info:
            await execute_with_retry(
                do_request,
                _render,
                breaker=breaker,
                provider="openrouter",
                max_retries=1,
                backoff_base=0.0,
            )

    err = exc_info.value
    assert err.retry_after == 7.0, f"Expected retry_after=7.0, got {err.retry_after!r}"
    assert isinstance(err, UpstreamUnavailableError), (
        "UpstreamRateLimitedError must be a subclass of UpstreamUnavailableError"
    )


async def test_retry_429_no_header_retry_after_none() -> None:
    """RP-2: exhausting retries on a 429 with NO Retry-After header → retry_after is None."""
    from gateway.proxy.domain.errors import UpstreamRateLimitedError  # noqa: PLC0415

    do_request = _seq_do_request(
        [
            _make_json_response(429, {"error": "rate limited"}),
        ]
    )
    breaker = _CountingBreaker()
    with pytest.raises(UpstreamRateLimitedError) as exc_info:
        await execute_with_retry(
            do_request,
            _render,
            breaker=breaker,
            provider="openrouter",
            max_retries=0,  # single attempt, immediate exhaust
            backoff_base=0.0,
        )

    assert exc_info.value.retry_after is None


async def test_retry_5xx_exhaust_raises_plain_unavailable() -> None:
    """RP-3 (regression guard): exhausting on 503 still raises plain UpstreamUnavailableError
    (NOT UpstreamRateLimitedError) — the 502 path is unchanged.
    """
    from gateway.proxy.domain.errors import UpstreamRateLimitedError  # noqa: PLC0415

    do_request = _seq_do_request(
        [
            _make_json_response(503, {"error": "overloaded"}),
        ]
    )
    breaker = _CountingBreaker()
    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await execute_with_retry(
            do_request,
            _render,
            breaker=breaker,
            provider="openrouter",
            max_retries=0,
            backoff_base=0.0,
        )

    # Must NOT be the rate-limit subclass.
    assert not isinstance(exc_info.value, UpstreamRateLimitedError), (
        "5xx exhaust must raise plain UpstreamUnavailableError, not UpstreamRateLimitedError"
    )


# ===========================================================================
# GROUP B — FallbackModelRouter.complete() alias-group exhaustion
# Tests: FR-1, FR-2, FR-3
# ===========================================================================


class _RaisingUpstream:
    """Fake CompletionUpstream that raises a pre-configured sequence of exceptions.

    Mirrors the PlanStreamUpstream approach but for complete() calls.
    """

    def __init__(self, exc_sequence: list[Exception]) -> None:
        self._seq = list(exc_sequence)
        self._idx = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        exc = self._seq[self._idx % len(self._seq)]
        self._idx += 1
        raise exc

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        raise NotImplementedError


async def test_alias_exhaust_all_ratelimited_raises_max_retry_after() -> None:
    """FR-1: all alias-group candidates fail with UpstreamRateLimitedError → router raises
    UpstreamRateLimitedError with retry_after == MAX(seen retry_after values).

    Candidate A: retry_after=4.0, candidate B: retry_after=9.0 → expect 9.0.
    """
    from gateway.proxy.domain.errors import UpstreamRateLimitedError  # noqa: PLC0415

    exc_a = UpstreamRateLimitedError("a rate-limited", retry_after=4.0)
    exc_b = UpstreamRateLimitedError("b rate-limited", retry_after=9.0)
    upstream = _RaisingUpstream([exc_a, exc_b])

    router = FallbackModelRouter(
        upstream=upstream,  # type: ignore[arg-type]
        model_groups={"fast": ["model-a", "model-b"]},
    )

    with pytest.raises(UpstreamRateLimitedError) as exc_info:
        await router.complete({"model": "fast", "messages": [{"role": "user", "content": "hi"}]})

    assert exc_info.value.retry_after == 9.0, (
        f"Expected max retry_after=9.0, got {exc_info.value.retry_after!r}"
    )


async def test_alias_exhaust_no_ratelimit_stays_generic() -> None:
    """FR-2: all candidates fail with plain UpstreamUnavailableError → router raises
    plain UpstreamUnavailableError, NOT UpstreamRateLimitedError (502 path unchanged).
    """
    from gateway.proxy.domain.errors import UpstreamRateLimitedError  # noqa: PLC0415

    upstream = _RaisingUpstream(
        [UpstreamUnavailableError("a down"), UpstreamUnavailableError("b down")]
    )

    router = FallbackModelRouter(
        upstream=upstream,  # type: ignore[arg-type]
        model_groups={"fast": ["model-a", "model-b"]},
    )

    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await router.complete({"model": "fast", "messages": [{"role": "user", "content": "hi"}]})

    assert not isinstance(exc_info.value, UpstreamRateLimitedError), (
        "All-5xx exhaustion must raise plain UpstreamUnavailableError"
    )


async def test_alias_exhaust_mixed_surfaces_ratelimit() -> None:
    """FR-3: one 5xx candidate + one UpstreamRateLimitedError(retry_after=6) →
    router raises UpstreamRateLimitedError with retry_after == 6.0.
    """
    from gateway.proxy.domain.errors import UpstreamRateLimitedError  # noqa: PLC0415

    exc_5xx = UpstreamUnavailableError("model-a 5xx")
    exc_rl = UpstreamRateLimitedError("model-b rate-limited", retry_after=6.0)
    upstream = _RaisingUpstream([exc_5xx, exc_rl])

    router = FallbackModelRouter(
        upstream=upstream,  # type: ignore[arg-type]
        model_groups={"fast": ["model-a", "model-b"]},
    )

    with pytest.raises(UpstreamRateLimitedError) as exc_info:
        await router.complete({"model": "fast", "messages": [{"role": "user", "content": "hi"}]})

    assert exc_info.value.retry_after == 6.0, (
        f"Expected retry_after=6.0, got {exc_info.value.retry_after!r}"
    )


# ===========================================================================
# GROUP C — open_resilient_stream: last-attempt UpstreamRateLimitedError propagates
# Test: SR-1
# ===========================================================================


async def test_stream_resilient_reraises_ratelimit() -> None:
    """SR-1: when the LAST pre-first-byte attempt raises UpstreamRateLimitedError(retry_after=2.0),
    open_resilient_stream propagates it unchanged (locks the existing bare `raise`).

    This confirms NO new code is required in streaming_resilience.py — the subclass
    passes through the existing re-raise at line 59 of that module.
    """
    from gateway.proxy.domain.errors import UpstreamRateLimitedError  # noqa: PLC0415

    rate_limit_exc = UpstreamRateLimitedError("rate-limited pre-byte", retry_after=2.0)

    async def open_stream(model_id: str) -> AsyncIterator[bytes]:
        raise rate_limit_exc
        yield  # make it an async generator  # noqa: unreachable

    with pytest.raises(UpstreamRateLimitedError) as exc_info:
        await open_resilient_stream(
            attempts=["model-a"],
            open_stream=open_stream,  # type: ignore[arg-type]
        )

    propagated = exc_info.value
    assert propagated is rate_limit_exc, (
        "open_resilient_stream must re-raise the exact UpstreamRateLimitedError instance"
    )
    assert propagated.retry_after == 2.0


# ===========================================================================
# GROUP D — end-to-end HTTP use-case tests (DB-backed via shared conftest fixtures)
# Tests: UC-1, UC-2, UC-3, UC-4
# These mirror test_proxy_completions.py patterns: install FakeCompletionUpstream,
# POST to /v1/chat/completions, assert status / body code / headers / usage rows.
# ===========================================================================

COMPLETIONS = "/v1/chat/completions"


class _RaisingCompletionUpstream:
    """Fake CompletionUpstream that always raises a given exception on complete()
    or raises it pre-first-byte on stream().

    Mirrors FakeCompletionUpstream from test_proxy_completions.py but raises instead.
    """

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        raise self._exc

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        exc = self._exc

        async def _gen() -> AsyncIterator[bytes]:
            raise exc
            yield  # noqa: unreachable

        return _gen()


class _FakeUsageRecorder:
    """In-memory usage recorder (mirrors FakeUsageRecorder from test_proxy_completions.py)."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        usage: dict[str, Any] | None,
        status: int,
        **_kwargs: Any,
    ) -> None:
        self.records.append(
            {
                "tenant_id": tenant_id,
                "key_id": key_id,
                "model": model,
                "usage": usage,
                "status": status,
            }
        )


def _payload(model: str, stream: bool | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": "hello"}]}
    if stream is not None:
        body["stream"] = stream
    return body


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _assert_problem(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status, f"Expected HTTP {status}, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("code") == code, f"Expected code={code!r}, got {data.get('code')!r}: {data}"


@pytest.fixture
async def api_key(client: httpx.AsyncClient) -> dict[str, str]:
    """Create a tenant + API key (mirrors api_key fixture in test_proxy_completions.py)."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "RateAcme",
            "email": "rl@acme.io",
            "password": "correct horse battery",
        },
    )
    assert signup.status_code == 201, f"Signup failed: {signup.text}"
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "rl@acme.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys", json={"name": "ci-rl"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert created.status_code == 201, f"Key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert a single active model + pricing row (mirrors active_model fixture)."""
    model_id = "openai/gpt-4o"
    await db_session.execute(
        text("INSERT INTO models (id, name, context_length, active) VALUES (:i, :n, 128000, true)"),
        {"i": model_id, "n": "GPT-4o-rl"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots "
            "(id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at) "
            "VALUES (:id, :m, 0.0000025, 0.00001, now())"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()
    return model_id


async def test_nonstream_ratelimit_maps_429_and_header(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
) -> None:
    """UC-1: upstream.complete() raises UpstreamRateLimitedError(retry_after=5.0) →
    non-stream response is HTTP 429 + code ERR_UPSTREAM_RATE_LIMITED + Retry-After: 5
    + 1 usage_records row with status=429.

    RED until: UPSTREAM_RATE_LIMITED ErrorSpec + use_cases non-stream catch site.
    """
    from gateway.proxy.domain.errors import UpstreamRateLimitedError  # noqa: PLC0415

    exc = UpstreamRateLimitedError("upstream rate limited", retry_after=5.0)
    app.state.completion_upstream = _RaisingCompletionUpstream(exc)
    recorder = _FakeUsageRecorder()
    app.state.usage_recorder = recorder

    resp = await client.post(
        COMPLETIONS, json=_payload(active_model), headers=_auth(api_key["key"])
    )

    _assert_problem(resp, 429, "ERR_UPSTREAM_RATE_LIMITED")
    assert resp.headers.get("retry-after") == "5", (
        f"Expected Retry-After: 5, got {resp.headers.get('retry-after')!r}"
    )
    # One usage row with status=429.
    assert len(recorder.records) == 1, f"Expected 1 usage row, got {len(recorder.records)}"
    assert recorder.records[0]["status"] == 429
    assert recorder.records[0]["usage"] is None


async def test_nonstream_ratelimit_no_header_when_none(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
) -> None:
    """UC-2: UpstreamRateLimitedError(retry_after=None) → 429 + NO Retry-After header.

    RED until: use_cases non-stream catch site with conditional header emission.
    """
    from gateway.proxy.domain.errors import UpstreamRateLimitedError  # noqa: PLC0415

    exc = UpstreamRateLimitedError("upstream rate limited, no header", retry_after=None)
    app.state.completion_upstream = _RaisingCompletionUpstream(exc)
    recorder = _FakeUsageRecorder()
    app.state.usage_recorder = recorder

    resp = await client.post(
        COMPLETIONS, json=_payload(active_model), headers=_auth(api_key["key"])
    )

    _assert_problem(resp, 429, "ERR_UPSTREAM_RATE_LIMITED")
    assert "retry-after" not in resp.headers, (
        f"Retry-After header must be absent when retry_after=None, got: {dict(resp.headers)}"
    )


async def test_stream_prebyte_ratelimit_maps_429(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
) -> None:
    """UC-3: upstream.stream() raises UpstreamRateLimitedError(retry_after=3.0) pre-first-byte →
    stream response is HTTP 429 + code ERR_UPSTREAM_RATE_LIMITED + Retry-After: 3.

    Status not yet committed at the pre-first-byte boundary, so 429 is still settable.
    RED until: use_cases pre-first-byte stream catch site.
    """
    from gateway.proxy.domain.errors import UpstreamRateLimitedError  # noqa: PLC0415

    exc = UpstreamRateLimitedError("stream rate limited pre-byte", retry_after=3.0)
    app.state.completion_upstream = _RaisingCompletionUpstream(exc)

    resp = await client.post(
        COMPLETIONS, json=_payload(active_model, stream=True), headers=_auth(api_key["key"])
    )

    _assert_problem(resp, 429, "ERR_UPSTREAM_RATE_LIMITED")
    assert resp.headers.get("retry-after") == "3", (
        f"Expected Retry-After: 3, got {resp.headers.get('retry-after')!r}"
    )


async def test_nonstream_5xx_still_502(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
) -> None:
    """UC-4 (regression guard): plain UpstreamUnavailableError → HTTP 502 ERR_UPSTREAM_UNAVAILABLE.

    The rate-limit path must NOT affect the existing 502 behavior for non-429 failures.
    """
    exc = UpstreamUnavailableError("upstream 5xx fail")
    app.state.completion_upstream = _RaisingCompletionUpstream(exc)

    resp = await client.post(
        COMPLETIONS, json=_payload(active_model), headers=_auth(api_key["key"])
    )

    _assert_problem(resp, 502, "ERR_UPSTREAM_UNAVAILABLE")
