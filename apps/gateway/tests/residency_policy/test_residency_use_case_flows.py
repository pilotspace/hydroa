"""CompletionUseCase / EmbeddingsUseCase / NonChatGovernance residency flow suite.

Fakes only — NO DB/Redis (mirrors tests/cache_alias_billing, tests/deployment_limits
DL2b idiom: assert at the use-case boundary where the §3 mapping is contractually
specified, not via a full endpoint+Postgres harness). Covers the frozen §2 scenarios
for chat alias/plain, cache re-validation (M7), streaming (both paths), embeddings,
realtime-relay-shaped governance, and the ZDR-composition/concurrent-race scenarios.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from gateway.core.errors import ProblemError
from gateway.keys.domain.entities import AuthzResult
from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.governance import NonChatGovernance
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.domain.ports import ModelAccess

TENANT_EU = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
KEY_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
ALIAS = "chat-default"
US_CANDIDATE = "anthropic/claude-opus-4-us"
EU_CANDIDATE = "anthropic/claude-opus-4-eu"
PLAIN_US = "vendor/plain-us-model"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResidencyLookup:
    def __init__(self, *, pin: str | None, regions: dict[str, str]) -> None:
        self.pin = pin
        self.regions = regions

    async def tenant_pin(self, tenant_id: uuid.UUID) -> str | None:
        return self.pin

    async def regions_for(self, model_ids: list[str]) -> dict[str, str]:
        return {m: self.regions[m] for m in model_ids if m in self.regions}


class FakeAuthenticator:
    def __init__(self, *, tenant_id: uuid.UUID = TENANT_EU, cache_enabled: bool = False) -> None:
        self._tenant_id = tenant_id
        self._cache_enabled = cache_enabled

    async def authenticate(self, raw_key: str) -> AuthzResult:
        return AuthzResult(
            tenant_id=self._tenant_id, key_id=KEY_ID, cache_enabled=self._cache_enabled
        )


class FakeModelChecker:
    """Every model id known to this suite is ACTIVE — governance never blocks on catalog."""

    async def is_active(self, model_id: str) -> bool:
        return True

    async def check_for_tenant(self, model_id: str, tenant_id: uuid.UUID) -> ModelAccess:
        return ModelAccess.ACTIVE


class FakeUsageRecorder:
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
        self.records.append({"tenant_id": tenant_id, "model": model, "status": status})


class FakeResponseCache:
    """In-memory exact-match ResponseCache (get/set only — no pointer/semantic)."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        cached = self.store.get(cache_key)
        return dict(cached) if cached is not None else None

    async def set(self, cache_key: str, body: dict[str, Any], ttl_seconds: int) -> None:
        self.store[cache_key] = dict(body)


class _Upstream:
    def __init__(self, body: dict[str, Any] | None = None) -> None:
        self.body = body or {"choices": [], "usage": {"total_tokens": 5}}
        self.calls: list[dict[str, Any]] = []

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append(dict(payload))
        return 200, {**self.body, "model": payload.get("model")}

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls.append(dict(payload))

        async def _gen() -> AsyncIterator[bytes]:
            yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


def _payload(model: str) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


def _model_of(resp_body: Any) -> Any:
    """complete()'s 2nd tuple element is `dict[str, Any] | BatchDivertedStream` —
    batch diversion never engages in this suite (no batch_processor wired), so it is
    always the plain dict; narrow it here once instead of at every call site."""
    assert isinstance(resp_body, dict), f"expected a plain dict, got {type(resp_body)}"
    return resp_body["model"]


async def settle() -> None:
    """Let a fire-and-forget cache.set() task (asyncio.ensure_future in
    _fire_cache_set) actually run before the next call reads the cache — mirrors
    tests/stream_alias_billing/conftest.py's identical helper."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def _use_case(
    *, residency_lookup: Any, cache: Any = None
) -> tuple[CompletionUseCase, FakeAuthenticator]:
    auth = FakeAuthenticator(cache_enabled=cache is not None)
    uc = CompletionUseCase(
        authenticator=auth,
        model_checker=FakeModelChecker(),
        response_cache=cache,
        residency_lookup=residency_lookup,
    )
    return uc, auth


def _router(residency_lookup: Any, upstream: _Upstream) -> FallbackModelRouter:
    return FallbackModelRouter(
        upstream=upstream,
        model_groups={ALIAS: [US_CANDIDATE, EU_CANDIDATE]},
        residency_lookup=residency_lookup,
    )


# ===========================================================================
# Chat alias — EU-pinned tenant served only by eu candidate (M4, M6)
# ===========================================================================


async def test_eu_pinned_alias_served_only_by_eu_candidate() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={US_CANDIDATE: "us", EU_CANDIDATE: "eu"})
    uc, _auth = _use_case(residency_lookup=lookup)
    upstream = _Upstream()
    router = _router(lookup, upstream)
    recorder = FakeUsageRecorder()

    status, resp_body, _x_cache = await uc.complete(
        raw_key="k",
        body=_payload(ALIAS),
        upstream=upstream,
        usage_recorder=recorder,
        model_router=router,
    )

    assert status == 200
    assert _model_of(resp_body) == EU_CANDIDATE
    assert upstream.calls == [
        {"model": EU_CANDIDATE, "messages": [{"role": "user", "content": "hi"}]}
    ]


# ===========================================================================
# Chat alias — zero eligible candidates refused, never rerouted (M4, R1)
# ===========================================================================


async def test_eu_pinned_alias_zero_eligible_refused_never_rerouted() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={US_CANDIDATE: "us", EU_CANDIDATE: "us"})
    uc, _auth = _use_case(residency_lookup=lookup)
    upstream = _Upstream()
    router = _router(lookup, upstream)
    recorder = FakeUsageRecorder()

    with pytest.raises(ProblemError) as exc_info:
        await uc.complete(
            raw_key="k",
            body=_payload(ALIAS),
            upstream=upstream,
            usage_recorder=recorder,
            model_router=router,
        )

    assert exc_info.value.status == 403
    assert exc_info.value.code == "ERR_RESIDENCY_NO_ELIGIBLE_REGION"
    assert upstream.calls == [], "neither us candidate may ever be dialed"
    assert recorder.records == [], "a refused request must never produce a usage record"


# ===========================================================================
# Chat plain model — out-of-region rejected at governance, before cache/upstream (M4, R1)
# ===========================================================================


async def test_eu_pinned_plain_out_of_region_rejected_before_upstream() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={PLAIN_US: "us"})
    uc, _auth = _use_case(residency_lookup=lookup)
    upstream = _Upstream()
    recorder = FakeUsageRecorder()

    with pytest.raises(ProblemError) as exc_info:
        await uc.complete(
            raw_key="k",
            body=_payload(PLAIN_US),
            upstream=upstream,
            usage_recorder=recorder,
        )

    assert exc_info.value.status == 403
    assert exc_info.value.code == "ERR_RESIDENCY_NO_ELIGIBLE_REGION"
    assert upstream.calls == [], "governance must reject before any upstream call"
    assert recorder.records == []


# ===========================================================================
# Unpinned tenant — byte-identical to pre-residency-policy behavior (After-state, M1)
# ===========================================================================


async def test_unpinned_tenant_byte_identical_no_filtering() -> None:
    lookup = FakeResidencyLookup(pin=None, regions={US_CANDIDATE: "us", EU_CANDIDATE: "eu"})
    uc, _auth = _use_case(residency_lookup=lookup)
    upstream = _Upstream()
    router = _router(lookup, upstream)
    recorder = FakeUsageRecorder()

    status, resp_body, _x_cache = await uc.complete(
        raw_key="k",
        body=_payload(ALIAS),
        upstream=upstream,
        usage_recorder=recorder,
        model_router=router,
    )

    assert status == 200
    assert _model_of(resp_body) == US_CANDIDATE, (
        "OrderedStrategy default (first candidate), unfiltered"
    )


# ===========================================================================
# Unknown-region candidates never satisfy a specific pin (M6, R4)
# ===========================================================================


async def test_unknown_region_candidates_never_satisfy_pin() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={EU_CANDIDATE: "global"})  # US_CANDIDATE absent
    uc, _auth = _use_case(residency_lookup=lookup)
    upstream = _Upstream()
    router = _router(lookup, upstream)
    recorder = FakeUsageRecorder()

    with pytest.raises(ProblemError) as exc_info:
        await uc.complete(
            raw_key="k",
            body=_payload(ALIAS),
            upstream=upstream,
            usage_recorder=recorder,
            model_router=router,
        )
    assert exc_info.value.status == 403
    assert upstream.calls == []


# ===========================================================================
# Stale cache hit produced before a tighter policy is never replayed (M7)
# ===========================================================================


async def test_stale_cache_hit_before_tighter_policy_never_replayed() -> None:
    """A response cached while unpinned (served by a us deployment) must NOT be
    replayed once the tenant tightens to eu — degrades to a MISS, falls through to
    fresh routing."""
    cache = FakeResponseCache()
    # First call: unpinned tenant, served by us candidate, response gets cached.
    lookup = FakeResidencyLookup(pin=None, regions={US_CANDIDATE: "us", EU_CANDIDATE: "eu"})
    uc, _auth = _use_case(residency_lookup=lookup, cache=cache)
    upstream = _Upstream()
    router = _router(lookup, upstream)
    recorder = FakeUsageRecorder()

    status1, resp_body1, _x_cache1 = await uc.complete(
        raw_key="k",
        body=_payload(ALIAS),
        upstream=upstream,
        usage_recorder=recorder,
        model_router=router,
    )
    assert status1 == 200
    assert _model_of(resp_body1) == US_CANDIDATE
    await settle()  # let the fire-and-forget cache.set() task actually run
    assert len(cache.store) == 1, "response must have been cached"

    # Tenant tightens the pin to "eu" (no code change needed -- lookup is FRESH per-call).
    lookup.pin = "eu"

    status2, resp_body2, x_cache2 = await uc.complete(
        raw_key="k",
        body=_payload(ALIAS),
        upstream=upstream,
        usage_recorder=recorder,
        model_router=router,
    )

    assert status2 == 200
    assert x_cache2 == "miss", "cache hit degraded to MISS -> fresh eu-filtered routing"
    assert _model_of(resp_body2) == EU_CANDIDATE
    assert upstream.calls[-1]["model"] == EU_CANDIDATE
    assert len(upstream.calls) == 2, "the stale us-origin body must never be returned verbatim"


async def test_matching_region_cache_hit_still_replayed() -> None:
    """Sanity: a cache hit whose region STILL satisfies the current pin is a normal HIT
    (no upstream call on the second request)."""
    cache = FakeResponseCache()
    lookup = FakeResidencyLookup(pin="eu", regions={EU_CANDIDATE: "eu"})
    uc, _auth = _use_case(residency_lookup=lookup, cache=cache)
    upstream = _Upstream()
    router = _router(lookup, upstream)
    recorder = FakeUsageRecorder()

    body = _payload(ALIAS)
    # Force a single-candidate alias so the served candidate is deterministic.
    router = FallbackModelRouter(
        upstream=upstream, model_groups={ALIAS: [EU_CANDIDATE]}, residency_lookup=lookup
    )

    await uc.complete(
        raw_key="k", body=body, upstream=upstream, usage_recorder=recorder, model_router=router
    )
    assert len(upstream.calls) == 1
    await settle()  # let the fire-and-forget cache.set() task actually run

    _status2, _resp2, x_cache2 = await uc.complete(
        raw_key="k", body=body, upstream=upstream, usage_recorder=recorder, model_router=router
    )
    assert len(upstream.calls) == 1, "second call must be a genuine cache HIT, no upstream call"
    assert x_cache2 == "hit"


# ===========================================================================
# Default (non-resilient) streaming never picks an out-of-region primary (Issue #6)
# ===========================================================================


async def test_default_stream_never_picks_out_of_region_primary() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={US_CANDIDATE: "us", EU_CANDIDATE: "eu"})
    uc, _auth = _use_case(residency_lookup=lookup)
    upstream = _Upstream()
    router = _router(lookup, upstream)  # OrderedStrategy -> us would be primary, unfiltered
    recorder = FakeUsageRecorder()

    chunks = []
    gen = await uc.stream(
        raw_key="k",
        body=_payload(ALIAS),
        upstream=upstream,
        usage_recorder=recorder,
        model_router=router,
    )
    async for chunk in gen:
        chunks.append(chunk)

    assert upstream.calls[0]["model"] == EU_CANDIDATE, "primary must be eu, never us"


async def test_default_stream_zero_eligible_raises_403_not_502() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={US_CANDIDATE: "us"})
    uc, _auth = _use_case(residency_lookup=lookup)
    upstream = _Upstream()
    router = _router(lookup, upstream)
    recorder = FakeUsageRecorder()

    with pytest.raises(ProblemError) as exc_info:
        await uc.stream(
            raw_key="k",
            body=_payload(ALIAS),
            upstream=upstream,
            usage_recorder=recorder,
            model_router=router,
        )

    assert exc_info.value.status == 403
    assert exc_info.value.code == "ERR_RESIDENCY_NO_ELIGIBLE_REGION"
    assert upstream.calls == []


# ===========================================================================
# Pre-first-byte resilient streaming never opens an out-of-region candidate (Issue #6)
# ===========================================================================


async def test_resilient_stream_never_opens_out_of_region_candidate() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={US_CANDIDATE: "us", EU_CANDIDATE: "eu"})
    uc = CompletionUseCase(
        authenticator=FakeAuthenticator(),
        model_checker=FakeModelChecker(),
        residency_lookup=lookup,
        stream_resilience_enabled=True,
    )
    upstream = _Upstream()
    router = _router(lookup, upstream)
    recorder = FakeUsageRecorder()

    gen = await uc.stream(
        raw_key="k",
        body=_payload(ALIAS),
        upstream=upstream,
        usage_recorder=recorder,
        model_router=router,
    )
    async for _ in gen:
        pass

    assert all(c["model"] != US_CANDIDATE for c in upstream.calls), (
        "the us candidate must never be opened, not even as a pre-first-byte fallover"
    )
    assert upstream.calls[0]["model"] == EU_CANDIDATE


# ===========================================================================
# Non-chat governance (embeddings/images/audio/realtime-relay-shaped) — Tier 1 (M4)
# ===========================================================================


def _governance(residency_lookup: Any) -> NonChatGovernance:
    return NonChatGovernance(
        authenticator=FakeAuthenticator(),
        model_checker=FakeModelChecker(),
        budget_guard=_PassthroughBudget(),
        rate_limiter=None,
        redis_client=None,
        residency_lookup=residency_lookup,
    )


class _PassthroughBudget:
    async def check(self, tenant_id: uuid.UUID) -> None:
        return None


async def test_embeddings_shaped_governance_rejects_out_of_region_before_catalog_query() -> None:
    """embeddings/images/audio/realtime-relay/realtime-WS all share this ONE governance
    entry — the existence check fires here, before any pipeline-specific catalog/routing
    lookup ever runs (M4, R1)."""
    lookup = FakeResidencyLookup(pin="eu", regions={PLAIN_US: "us"})
    gov = _governance(lookup)

    with pytest.raises(ProblemError) as exc_info:
        await gov.authorize(raw_key="k", model_id=PLAIN_US, estimated_tokens=1)

    assert exc_info.value.status == 403
    assert exc_info.value.code == "ERR_RESIDENCY_NO_ELIGIBLE_REGION"


async def test_realtime_relay_shaped_governance_fails_closed_on_unset_region() -> None:
    """Realtime relay dials a single hardcoded model with no alias/candidate list; a
    catalog row with NO region tag must fail closed for a pinned tenant (M6, Issue #4, R4)."""
    lookup = FakeResidencyLookup(pin="eu", regions={})  # unset region -> never eligible
    gov = _governance(lookup)

    with pytest.raises(ProblemError) as exc_info:
        await gov.authorize(raw_key="k", model_id="openai/gpt-realtime", estimated_tokens=None)

    assert exc_info.value.status == 403
    assert exc_info.value.code == "ERR_RESIDENCY_NO_ELIGIBLE_REGION"


async def test_non_chat_governance_unpinned_tenant_passes() -> None:
    lookup = FakeResidencyLookup(pin=None, regions={})
    gov = _governance(lookup)
    authz = await gov.authorize(raw_key="k", model_id=PLAIN_US, estimated_tokens=1)
    assert authz.tenant_id == TENANT_EU


# ===========================================================================
# Concurrent policy tightening mid-flight — documented residual, not silently masked (M5)
# ===========================================================================


async def test_concurrent_policy_tightening_is_fresh_per_call_not_retroactive() -> None:
    """M5: the residency decision is a FRESH per-call read — a request already past the
    existence check when the policy tightens completes under the OLD (permissive) policy;
    the VERY NEXT request is evaluated under the NEW policy. This is the accepted TOCTOU
    residual (Issue #7), proven here as two sequential calls against a MUTATING fake."""
    lookup = FakeResidencyLookup(pin=None, regions={US_CANDIDATE: "us", EU_CANDIDATE: "eu"})
    uc, _auth = _use_case(residency_lookup=lookup)
    upstream = _Upstream()
    router = _router(lookup, upstream)
    recorder = FakeUsageRecorder()

    # Request 1 begins under NULL policy (passes, unrestricted).
    status1, resp_body1, _x1 = await uc.complete(
        raw_key="k",
        body=_payload(ALIAS),
        upstream=upstream,
        usage_recorder=recorder,
        model_router=router,
    )
    assert status1 == 200
    assert _model_of(resp_body1) == US_CANDIDATE

    # Policy tightens between requests (simulates the admin PUT committing).
    lookup.pin = "eu"

    # Request 2 (the VERY NEXT request) is evaluated fresh and correctly restricted.
    status2, resp_body2, _x2 = await uc.complete(
        raw_key="k",
        body=_payload(ALIAS),
        upstream=upstream,
        usage_recorder=recorder,
        model_router=router,
    )
    assert status2 == 200
    assert _model_of(resp_body2) == EU_CANDIDATE
