"""Unit tests for gateway.proxy.application.residency (shared Tier 1 / Tier 2 logic).

No DB/Redis — pure logic against a FakeResidencyLookup, mirroring the
deployment_limits suite's use-case-layer unit-test idiom (DL2b rationale: assert at
the boundary where the mapping is contractually specified, not via a full endpoint
harness).
"""

from __future__ import annotations

import uuid

import pytest

from gateway.core.error_catalog import RESIDENCY_NO_ELIGIBLE_REGION
from gateway.core.errors import ProblemError
from gateway.proxy.application.residency import (
    cache_hit_region_ok,
    check_residency_existence,
    filter_candidates_by_region,
    region_satisfies_pin,
)

TENANT = uuid.uuid4()


class FakeResidencyLookup:
    def __init__(self, *, pin: str | None, regions: dict[str, str]) -> None:
        self.pin = pin
        self.regions = regions
        self.tenant_pin_calls = 0
        self.regions_for_calls: list[list[str]] = []

    async def tenant_pin(self, tenant_id: uuid.UUID) -> str | None:
        self.tenant_pin_calls += 1
        return self.pin

    async def regions_for(self, model_ids: list[str]) -> dict[str, str]:
        self.regions_for_calls.append(list(model_ids))
        return {m: self.regions[m] for m in model_ids if m in self.regions}


# ===========================================================================
# region_satisfies_pin — M6
# ===========================================================================


def test_no_pin_always_satisfied() -> None:
    assert region_satisfies_pin(None, "us") is True
    assert region_satisfies_pin(None, None) is True
    assert region_satisfies_pin(None, "global") is True


def test_global_never_satisfies_a_specific_pin() -> None:
    """M6: `global` NEVER satisfies a pin — Tin-confirmed at freeze."""
    assert region_satisfies_pin("eu", "global") is False
    assert region_satisfies_pin("us", "global") is False
    assert region_satisfies_pin("ap", "global") is False


def test_unset_region_never_satisfies_a_specific_pin() -> None:
    """M6: NULL/unset region never satisfies a specific pin (R4)."""
    assert region_satisfies_pin("eu", None) is False


def test_matching_region_satisfies_pin() -> None:
    assert region_satisfies_pin("eu", "eu") is True
    assert region_satisfies_pin("ap", "ap") is True


def test_mismatched_specific_regions_do_not_satisfy() -> None:
    assert region_satisfies_pin("eu", "us") is False


# ===========================================================================
# check_residency_existence — Tier 1
# ===========================================================================


async def test_tier1_noop_when_residency_unwired() -> None:
    """None residency ⇒ zero DB interaction, byte-identical (no raise)."""
    await check_residency_existence(None, "vendor/model-a", TENANT, None)


async def test_tier1_noop_when_tenant_has_no_pin() -> None:
    lookup = FakeResidencyLookup(pin=None, regions={"vendor/model-a": "us"})
    await check_residency_existence(lookup, "vendor/model-a", TENANT, None)
    assert lookup.tenant_pin_calls == 1
    assert lookup.regions_for_calls == [], "no region lookup needed once pin is None"


async def test_tier1_plain_model_in_region_passes() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={"vendor/model-eu": "eu"})
    await check_residency_existence(lookup, "vendor/model-eu", TENANT, None)


async def test_tier1_plain_model_out_of_region_raises_403() -> None:
    """R1: plain out-of-region model -> 403 ERR_RESIDENCY_NO_ELIGIBLE_REGION."""
    lookup = FakeResidencyLookup(pin="eu", regions={"vendor/model-us": "us"})
    with pytest.raises(ProblemError) as exc_info:
        await check_residency_existence(lookup, "vendor/model-us", TENANT, None)
    assert exc_info.value.status == 403
    assert exc_info.value.code == "ERR_RESIDENCY_NO_ELIGIBLE_REGION"
    assert exc_info.value.code == RESIDENCY_NO_ELIGIBLE_REGION.code


async def test_tier1_alias_with_one_eligible_candidate_passes() -> None:
    """Alias existence check: >=1 in-region candidate anywhere in the group is enough."""
    lookup = FakeResidencyLookup(
        pin="eu", regions={"vendor/model-us": "us", "vendor/model-eu": "eu"}
    )
    await check_residency_existence(
        lookup,
        "chat-default",
        TENANT,
        {"chat-default": ["vendor/model-us", "vendor/model-eu"]},
    )


async def test_tier1_alias_zero_eligible_candidates_raises_403() -> None:
    """R1: no eu candidate anywhere in the group -> refused, never rerouted."""
    lookup = FakeResidencyLookup(
        pin="eu", regions={"vendor/model-us": "us", "vendor/model-us2": "us"}
    )
    with pytest.raises(ProblemError) as exc_info:
        await check_residency_existence(
            lookup,
            "chat-default",
            TENANT,
            {"chat-default": ["vendor/model-us", "vendor/model-us2"]},
        )
    assert exc_info.value.status == 403
    assert exc_info.value.code == "ERR_RESIDENCY_NO_ELIGIBLE_REGION"


async def test_tier1_alias_unknown_region_candidates_never_satisfy_pin() -> None:
    """M6/R4: global + NULL-region candidates are never eligible for a specific pin."""
    lookup = FakeResidencyLookup(pin="eu", regions={"vendor/model-b": "global"})
    with pytest.raises(ProblemError) as exc_info:
        await check_residency_existence(
            lookup,
            "chat-default",
            TENANT,
            {"chat-default": ["vendor/model-a", "vendor/model-b"]},
        )
    assert exc_info.value.status == 403
    assert exc_info.value.code == "ERR_RESIDENCY_NO_ELIGIBLE_REGION"


# ===========================================================================
# filter_candidates_by_region — Tier 2
# ===========================================================================


async def test_tier2_returns_candidates_unchanged_when_unwired() -> None:
    pin, filtered = await filter_candidates_by_region(None, ["a", "b"], TENANT)
    assert pin is None
    assert filtered == ["a", "b"]


async def test_tier2_returns_candidates_unchanged_when_no_pin() -> None:
    lookup = FakeResidencyLookup(pin=None, regions={"a": "us", "b": "eu"})
    pin, filtered = await filter_candidates_by_region(lookup, ["a", "b"], TENANT)
    assert pin is None
    assert filtered == ["a", "b"]


async def test_tier2_narrows_to_in_region_subset() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={"a": "us", "b": "eu"})
    pin, filtered = await filter_candidates_by_region(lookup, ["a", "b"], TENANT)
    assert pin == "eu"
    assert filtered == ["b"]


async def test_tier2_empty_when_no_candidate_matches() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={"a": "us", "b": "global"})
    pin, filtered = await filter_candidates_by_region(lookup, ["a", "b"], TENANT)
    assert pin == "eu"
    assert filtered == []


# ===========================================================================
# cache_hit_region_ok — M7
# ===========================================================================


async def test_cache_hit_ok_when_unwired() -> None:
    assert await cache_hit_region_ok(None, "vendor/model-us", TENANT) is True


async def test_cache_hit_ok_when_no_pin() -> None:
    lookup = FakeResidencyLookup(pin=None, regions={"vendor/model-us": "us"})
    assert await cache_hit_region_ok(lookup, "vendor/model-us", TENANT) is True


async def test_cache_hit_stale_cross_region_fails() -> None:
    """M7: a served region that no longer satisfies the CURRENT pin is not OK."""
    lookup = FakeResidencyLookup(pin="eu", regions={"vendor/model-us": "us"})
    assert await cache_hit_region_ok(lookup, "vendor/model-us", TENANT) is False


async def test_cache_hit_matching_region_ok() -> None:
    lookup = FakeResidencyLookup(pin="eu", regions={"vendor/model-eu": "eu"})
    assert await cache_hit_region_ok(lookup, "vendor/model-eu", TENANT) is True
