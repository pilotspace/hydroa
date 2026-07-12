"""FallbackModelRouter residency (Tier 2 dial-constraint filter) unit suite.

No DB/Redis — pure unit tests against FakeResidencyLookup, mirroring
tests/deployment_limits's use-case-layer idiom (limit_gate precedent this filter
mirrors exactly). Covers the frozen §2 scenarios that name complete()/stream()/
stream_resilient() explicitly.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.domain.errors import (
    AllCandidatesOutOfRegionError,
    UpstreamUnavailableError,
)

ALIAS = "chat-default"
US_CANDIDATE = "anthropic/claude-opus-4-us"
EU_CANDIDATE = "anthropic/claude-opus-4-eu"
TENANT = uuid.uuid4()


class FakeResidencyLookup:
    def __init__(self, *, pin: str | None, regions: dict[str, str]) -> None:
        self.pin = pin
        self.regions = regions

    async def tenant_pin(self, tenant_id: uuid.UUID) -> str | None:
        return self.pin

    async def regions_for(self, model_ids: list[str]) -> dict[str, str]:
        return {m: self.regions[m] for m in model_ids if m in self.regions}


class _Upstream:
    """Replay a sequence of (status, body) or exceptions; record payloads."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self._i = 0
        self.calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append(dict(payload))
        entry = self._outcomes[self._i]
        self._i += 1
        if isinstance(entry, BaseException):
            raise entry
        return entry  # type: ignore[return-value]

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.stream_calls.append(dict(payload))

        async def _gen() -> AsyncIterator[bytes]:
            yield b"chunk"

        return _gen()


def _body(model: str) -> dict[str, Any]:
    return {"id": f"gen-{model}", "model": model, "choices": [], "usage": {}}


def _router(residency_lookup: Any, upstream: _Upstream) -> FallbackModelRouter:
    return FallbackModelRouter(
        upstream=upstream,
        model_groups={ALIAS: [US_CANDIDATE, EU_CANDIDATE]},
        residency_lookup=residency_lookup,
    )


# ===========================================================================
# complete() — M4
# ===========================================================================


async def test_complete_narrows_to_eu_candidate_only() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={US_CANDIDATE: "us", EU_CANDIDATE: "eu"})
    up = _Upstream([(200, _body(EU_CANDIDATE))])
    router = _router(lookup, up)

    status, _body_out, served = await router.complete(
        {"model": ALIAS, "messages": []}, tenant_id=TENANT
    )

    assert status == 200
    assert served == EU_CANDIDATE
    assert up.calls == [{"model": EU_CANDIDATE, "messages": []}], (
        "the us candidate must NEVER be dialed"
    )


async def test_complete_zero_eligible_raises_out_of_region_never_dials() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={US_CANDIDATE: "us"})
    up = _Upstream([(200, _body(US_CANDIDATE))])
    router = _router(lookup, up)

    with pytest.raises(AllCandidatesOutOfRegionError) as exc_info:
        await router.complete({"model": ALIAS, "messages": []}, tenant_id=TENANT)

    assert exc_info.value.alias == ALIAS
    assert exc_info.value.region == "eu"
    assert up.calls == [], "no candidate may ever be dialed once the filter empties"


async def test_complete_no_tenant_id_is_unfiltered_byte_identical() -> None:
    """tenant_id=None (frozen callers that never pass it) -> zero residency interaction."""
    lookup = FakeResidencyLookup(pin="eu", regions={US_CANDIDATE: "us"})
    up = _Upstream([(200, _body(US_CANDIDATE))])
    router = _router(lookup, up)

    status, _body_out, served = await router.complete({"model": ALIAS, "messages": []})

    assert status == 200
    assert served == US_CANDIDATE


async def test_complete_unpinned_tenant_is_unfiltered() -> None:
    lookup = FakeResidencyLookup(pin=None, regions={US_CANDIDATE: "us", EU_CANDIDATE: "eu"})
    up = _Upstream([(200, _body(US_CANDIDATE))])
    router = _router(lookup, up)

    status, _b, served = await router.complete({"model": ALIAS, "messages": []}, tenant_id=TENANT)
    assert status == 200
    assert served == US_CANDIDATE, "OrderedStrategy default -> first candidate, unfiltered"


async def test_complete_unknown_region_candidates_filtered_out() -> None:
    """M6/R4: global/NULL-region candidates never satisfy a specific pin."""
    lookup = FakeResidencyLookup(
        pin="eu", regions={US_CANDIDATE: "global"}
    )  # EU_CANDIDATE absent -> unknown/NULL
    up = _Upstream([])
    router = _router(lookup, up)

    with pytest.raises(AllCandidatesOutOfRegionError):
        await router.complete({"model": ALIAS, "messages": []}, tenant_id=TENANT)
    assert up.calls == []


# ===========================================================================
# stream() — default (non-resilient) path, Issue #6
# ===========================================================================


async def test_stream_default_resolves_to_eu_primary_not_us() -> None:
    """stream() applies the SAME pre-loop filter as complete() before computing
    self._strategy_order(...)[0] — OrderedStrategy would otherwise pick the us
    candidate (candidates[0])."""
    lookup = FakeResidencyLookup(pin="eu", regions={US_CANDIDATE: "us", EU_CANDIDATE: "eu"})
    up = _Upstream([])
    router = _router(lookup, up)

    served_holder: list[str] = []
    residency_result = await router.residency_candidates(ALIAS, TENANT)
    assert residency_result is not None
    pin, candidates = residency_result
    assert pin == "eu"
    assert candidates == [EU_CANDIDATE]

    gen = router.stream(
        {"model": ALIAS, "messages": []},
        on_served=served_holder.append,
        candidates_override=candidates,
    )
    async for _ in gen:
        break

    assert served_holder == [EU_CANDIDATE]
    assert up.stream_calls[0]["model"] == EU_CANDIDATE


async def test_stream_empty_override_raises_synchronously() -> None:
    """stream()'s defensive raise on an empty override — synchronous, eager (F11
    contract preserved: no async DB call inside stream() itself)."""
    up = _Upstream([])
    router = _router(None, up)

    with pytest.raises(AllCandidatesOutOfRegionError):
        router.stream({"model": ALIAS, "messages": []}, candidates_override=[])
    assert up.stream_calls == [], "must never dial upstream on an empty override"


async def test_residency_candidates_none_when_unwired() -> None:
    up = _Upstream([])
    router = _router(None, up)
    assert await router.residency_candidates(ALIAS, TENANT) is None


async def test_residency_candidates_none_for_plain_model_id() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={})
    up = _Upstream([])
    router = _router(lookup, up)
    assert await router.residency_candidates("plain/model-not-an-alias", TENANT) is None


# ===========================================================================
# stream_resilient() — pre-first-byte fallover, Issue #6
# ===========================================================================


async def test_stream_resilient_attempts_only_eu_candidate() -> None:
    """open_resilient_stream must only ever receive the eu candidate as an attempt —
    the us candidate is never opened, not even as a pre-first-byte fallover attempt."""
    lookup = FakeResidencyLookup(pin="eu", regions={US_CANDIDATE: "us", EU_CANDIDATE: "eu"})
    up = _Upstream([])
    router = _router(lookup, up)

    served_holder: list[str] = []
    _first_chunk, gen = await router.stream_resilient(
        {"model": ALIAS, "messages": []},
        on_served=served_holder.append,
        tenant_id=TENANT,
    )
    async for _ in gen:
        break

    assert up.stream_calls[0]["model"] == EU_CANDIDATE
    assert all(c["model"] != US_CANDIDATE for c in up.stream_calls), (
        "the us candidate must NEVER be opened, not even as a fallover attempt"
    )


async def test_stream_resilient_zero_eligible_raises_before_any_open() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={US_CANDIDATE: "us"})
    up = _Upstream([])
    router = _router(lookup, up)

    with pytest.raises(AllCandidatesOutOfRegionError) as exc_info:
        await router.stream_resilient({"model": ALIAS, "messages": []}, tenant_id=TENANT)

    assert exc_info.value.region == "eu"
    assert up.stream_calls == []


# ===========================================================================
# Sanity: byte-identical when residency_lookup is None (every method)
# ===========================================================================


async def test_all_three_methods_byte_identical_when_residency_unwired() -> None:
    up1 = _Upstream([(200, _body(US_CANDIDATE))])
    router1 = _router(None, up1)
    status, _b, served = await router1.complete({"model": ALIAS, "messages": []}, tenant_id=TENANT)
    assert status == 200
    assert served == US_CANDIDATE

    up2 = _Upstream([])
    router2 = _router(None, up2)
    gen = router2.stream({"model": ALIAS, "messages": []})
    async for _ in gen:
        break
    assert up2.stream_calls[0]["model"] == US_CANDIDATE

    up3 = _Upstream([])
    router3 = _router(None, up3)
    _first_chunk, gen3 = await router3.stream_resilient(
        {"model": ALIAS, "messages": []}, tenant_id=TENANT
    )
    async for _ in gen3:
        break
    assert up3.stream_calls[0]["model"] == US_CANDIDATE


async def test_complete_still_raises_saturated_error_unaffected_by_residency() -> None:
    """Sanity: residency wiring must not disturb the pre-existing limit_gate /
    saturation error path (UpstreamUnavailableError -> normal fallover, no residency
    lookup, since residency_lookup=None here)."""
    up = _Upstream(
        [
            UpstreamUnavailableError("A down"),
            (200, _body(EU_CANDIDATE)),
        ]
    )
    router = _router(None, up)
    status, _b, served = await router.complete({"model": ALIAS, "messages": []})
    assert status == 200
    assert served == EU_CANDIDATE
