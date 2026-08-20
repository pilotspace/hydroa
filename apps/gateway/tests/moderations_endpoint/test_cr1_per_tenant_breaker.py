"""CR-1 (moderations-endpoint PLAN.md §6 GATE RECORD — Tin HARD-STOP 2026-07-24,
"fix now"): the moderation IO seam's CircuitBreaker is now keyed PER TENANT.

Before this change, `OpenAiModerationClient`/`OpenAIDirectProvider` shared exactly
ONE process-wide CircuitBreaker for every tenant's moderation traffic (both the
internal ml_moderation guardrail pre-call check AND the client-facing
/v1/moderations endpoint). One tenant driving 5 consecutive moderation failures —
e.g. a bad BYOK key, or a real upstream blip while THAT tenant happens to be
hammering the endpoint — opened the breaker for every OTHER tenant's moderation
traffic too: an availability leak through a shared failure-domain object.

ADDITIVE ONLY — this file adds new tests; it does not modify
tests/moderations_endpoint/test_moderations_endpoint.py, conftest.py, or any
frozen fixture. Unit-level (mirrors tests/guardrails/test_ml_moderation.py's own
`test_ml_moderation_breaker_open_short_circuits` / `test_ml_moderation_breaker_
isolated_from_completion_breaker` pattern): a real `OpenAiModerationClient` wraps
a real `OpenAIDirectProvider` whose `httpx.AsyncClient` is swapped for an
`httpx.MockTransport` — no real network. `set_guardrail_tenant_id` /
`reset_guardrail_tenant_id` simulate what `ModerationsUseCase.execute` and the
internal guardrail call sites (`nonchat_guardrails.py`/`use_cases.py`) do around
their own `moderate_raw()`/`moderate()` calls.

Run only this file:
  cd apps/gateway && uv run pytest tests/moderations_endpoint/test_cr1_per_tenant_breaker.py -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.domain.guardrail_tenant_context import (
    reset_guardrail_tenant_id,
    set_guardrail_tenant_id,
)
from gateway.proxy.domain.provider_credentials import BearerCredential
from gateway.proxy.infrastructure.ml_moderation_evaluator import OpenAiModerationClient
from gateway.proxy.infrastructure.openai_provider import OpenAIDirectProvider


def _build_failing_client() -> tuple[OpenAiModerationClient, list[httpx.Request]]:
    """Real OpenAiModerationClient + OpenAIDirectProvider over a MockTransport whose
    handler ALWAYS raises httpx.ReadTimeout — a single, deterministic,
    non-retryable transport failure per call (execute_with_retry's
    (ReadTimeout, WriteTimeout, NetworkError) branch calls
    `breaker.on_upstream_error()` exactly once and raises immediately, no retry
    sleep) — so N calls trip the breaker after exactly N failures, deterministically
    and fast.
    """
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        raise httpx.ReadTimeout("moderation upstream unreachable")

    provider = OpenAIDirectProvider(base_url="https://api.openai.com/v1")
    provider._client = httpx.AsyncClient(  # noqa: SLF001 — test double injection, same convention as tests/guardrails/test_ml_moderation.py
        base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler)
    )
    return OpenAiModerationClient(provider), captured


async def test_cr1_tenant_a_five_failures_never_open_tenant_bs_breaker() -> None:
    """The RED-FIRST scenario CR-1 names verbatim: tenant A drives 5 consecutive
    moderation failures -> tenant A's next call short-circuits (CircuitOpenError,
    honest degrade, no network dial), while tenant B's moderation call still
    dials the provider (its breaker stays CLOSED — unaffected by A's failures).

    Against PRE-CR-1 code (one process-wide breaker shared by every tenant) this
    fails at the tenant-B assertion: A's 5 failures already tripped the ONE shared
    breaker, so B's call ALSO short-circuits with CircuitOpenError instead of
    reaching the transport — the cross-tenant availability leak CR-1 closes.
    """
    ml_client, captured = _build_failing_client()

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    cred_token = set_provider_credential(BearerCredential(secret="sk-test"))  # noqa: S106
    try:
        # --- Tenant A drives 5 consecutive failures -> trips A's breaker OPEN ---
        gtid = set_guardrail_tenant_id(tenant_a)
        try:
            for i in range(5):
                with pytest.raises(UpstreamUnavailableError):
                    await ml_client.moderate_raw("hello", model="omni-moderation-latest")
                assert len(captured) == i + 1, (
                    f"expected the transport to be dialed on failure #{i + 1}"
                )
        finally:
            reset_guardrail_tenant_id(gtid)

        assert len(captured) == 5, f"expected exactly 5 dialed attempts, got {len(captured)}"

        # --- Tenant A's 6th call short-circuits: breaker OPEN, no network dial ---
        gtid = set_guardrail_tenant_id(tenant_a)
        try:
            with pytest.raises(CircuitOpenError):
                await ml_client.moderate_raw("hello again", model="omni-moderation-latest")
        finally:
            reset_guardrail_tenant_id(gtid)
        assert len(captured) == 5, (
            "tenant A's 6th call must short-circuit BEFORE any network dial "
            f"(breaker OPEN); transport was dialed {len(captured)} times, expected 5"
        )

        # --- Tenant B's call still reaches the transport: B's breaker is CLOSED ---
        gtid = set_guardrail_tenant_id(tenant_b)
        try:
            with pytest.raises(UpstreamUnavailableError):
                await ml_client.moderate_raw("hello from B", model="omni-moderation-latest")
        finally:
            reset_guardrail_tenant_id(gtid)
        assert len(captured) == 6, (
            "tenant B's call must reach the transport — its breaker must be unaffected "
            f"by tenant A's failures; transport was dialed {len(captured)} times, expected 6"
        )
    finally:
        reset_provider_credential(cred_token)


async def test_cr1_internal_guardrail_and_endpoint_share_one_breaker_per_tenant() -> None:
    """Invariant (b): for a GIVEN tenant, the internal guardrail check (`moderate()`)
    and the client-facing endpoint (`moderate_raw()`) must SHARE the same breaker
    instance — never two breakers for the same tenant+provider seam. Drives 3
    failures via `moderate()` (the internal-guardrail-shaped call) and 2 more via
    `moderate_raw()` (the endpoint-shaped call) for the SAME tenant — the 6th call
    (either shape) must short-circuit, proving the failure count accumulated on
    ONE shared breaker rather than resetting between the two call shapes.
    """
    ml_client, captured = _build_failing_client()
    tenant = uuid.uuid4()

    cred_token = set_provider_credential(BearerCredential(secret="sk-test"))  # noqa: S106
    try:
        gtid = set_guardrail_tenant_id(tenant)
        try:
            for _ in range(3):
                with pytest.raises(UpstreamUnavailableError):
                    await ml_client.moderate("hello")
            for _ in range(2):
                with pytest.raises(UpstreamUnavailableError):
                    await ml_client.moderate_raw("hello", model="omni-moderation-latest")
            assert len(captured) == 5

            # 6th failure (via either shape) must short-circuit — proves moderate()
            # and moderate_raw() accumulated failures on the SAME per-tenant breaker.
            with pytest.raises(CircuitOpenError):
                await ml_client.moderate("one more")
            assert len(captured) == 5, (
                "the 6th call must short-circuit without dialing the transport, "
                "proving moderate()/moderate_raw() share one breaker per tenant"
            )
        finally:
            reset_guardrail_tenant_id(gtid)
    finally:
        reset_provider_credential(cred_token)


async def test_cr1_eviction_never_resets_a_hot_tenants_open_breaker() -> None:
    """Bounded-registry invariant: eviction must not reset an OPEN breaker into
    effective CLOSED for a HOT tenant (one that keeps driving traffic through the
    registry). Fills the registry past capacity with OTHER tenants (each touched
    once, so they are all "colder" than the hot tenant by the time they're
    inserted) while periodically re-touching the hot tenant's own breaker — LRU
    eviction must never pick the hot tenant, so its OPEN state survives.
    """
    from gateway.proxy.infrastructure.ml_moderation_evaluator import (
        _MAX_TENANT_BREAKERS,
        _TenantBreakerRegistry,
    )

    registry = _TenantBreakerRegistry(max_size=8)  # small cap for a fast test
    hot_tenant = uuid.uuid4()

    hot_breaker = registry.get_or_create(hot_tenant)
    for _ in range(5):  # trip the hot tenant's breaker OPEN (threshold=5)
        hot_breaker.record_failure()
    assert hot_breaker.is_open() is True

    # Insert far more than capacity worth of OTHER tenants, re-touching the hot
    # tenant's entry periodically (as real per-request traffic would).
    for i in range(50):
        registry.get_or_create(uuid.uuid4())
        if i % 3 == 0:
            registry.get_or_create(hot_tenant)  # keep the hot tenant MRU

    assert len(registry) <= 8, f"registry exceeded its cap: {len(registry)}"
    still_hot_breaker = registry.get_or_create(hot_tenant)
    assert still_hot_breaker is hot_breaker, (
        "the hot tenant's breaker object must never be evicted/recreated while "
        "it keeps being touched — eviction must target only the LRU entry"
    )
    assert still_hot_breaker.is_open() is True, (
        "eviction must never reset a hot tenant's OPEN breaker into effective CLOSED"
    )

    # Sanity: the module's real default cap is the documented constant.
    assert _MAX_TENANT_BREAKERS == 4096


async def test_cr1_moderation_breaker_still_isolated_from_completion_breaker() -> None:
    """Regression guard for the invariant CR-1 must NOT break: the moderation
    seam's failure domain stays isolated from chat/embeddings' own breaker(s) —
    per-tenant keying is scoped entirely WITHIN the dedicated moderation
    OpenAIDirectProvider instance; it never touches a different provider instance.
    """
    moderation_provider = OpenAIDirectProvider(base_url="https://api.openai.com/v1")
    completion_provider = OpenAIDirectProvider(base_url="https://api.openai.com/v1")

    ml_client, captured = _build_failing_client()
    # Swap in the SAME failing transport as moderation_provider isn't otherwise used;
    # this test only asserts completion_provider's own breaker is never touched.
    del captured

    tenant = uuid.uuid4()
    cred_token = set_provider_credential(BearerCredential(secret="sk-test"))  # noqa: S106
    try:
        gtid = set_guardrail_tenant_id(tenant)
        try:
            for _ in range(5):
                with pytest.raises(UpstreamUnavailableError):
                    await ml_client.moderate("hello")
        finally:
            reset_guardrail_tenant_id(gtid)
    finally:
        reset_provider_credential(cred_token)

    # tenant-scoped-breaker-cooldown (R9 P0 #3): `provider._breaker` — one process-wide
    # CircuitBreaker per adapter instance — is GONE; a breaker is now resolved per tenant
    # through `_breaker_for()`. The assertions below are the SAME invariant re-expressed
    # against the new accessor, and are STRENGTHENED, never weakened: each provider is
    # now checked BOTH in the ambient (unattributed) context AND under the very tenant
    # whose moderation breaker was just tripped — the bucket a cross-instance leak would
    # actually land in. The original single-breaker check could not distinguish those.
    for label, provider in (
        ("completion_provider", completion_provider),
        ("moderation_provider", moderation_provider),
    ):
        assert provider._breaker_for().is_open() is False, (  # noqa: SLF001
            f"a per-tenant moderation breaker trip must never touch {label}'s own breaker"
        )
        gtid = set_guardrail_tenant_id(tenant)
        try:
            assert provider._breaker_for().is_open() is False, (  # noqa: SLF001
                f"{label} must be unaffected even in the TRIPPED tenant's own partition — "
                "the moderation trip belongs to ml_client's dedicated provider instance alone"
            )
        finally:
            reset_guardrail_tenant_id(gtid)
