"""Second independent adversarial verify — residency-bedrock-region-guard.

Distinct from test_bedrock_guard_adversarial_verify.py (1st verify pass): this file
empirically proves the REACHABILITY claim rather than asserting it statically. The
helper `_assert_region_consistent` is case-sensitive by construction (documented,
frozen contract). The open question every casing/whitespace/unicode-confusable
angle raises is: can a tenant actually get a case-mutated model id dispatched to
the REAL Bedrock adapter at all? This file wires the REAL
`ProviderAwareCompletionUpstream` + `CatalogProviderResolver` (the exact dispatch
seam production uses to pick an upstream adapter from ``payload["model"]``) with a
fake in-memory catalog and proves: every casing/whitespace/unicode-confusable
mutation of a real Bedrock cross-region-profile id fails the catalog's exact-match
lookup and is dispatched to the DEFAULT ("openrouter") adapter, never to Bedrock —
so the helper's case-sensitivity is not independently reachable through the
tenant-facing HTTP path today. Also covers the residency-policy fail-closed
`region_satisfies_pin` predicate, the layer that governs the "drop the prefix to
escape" question for unprefixed/direct-invoke Bedrock model ids.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from gateway.proxy.application.residency import region_satisfies_pin
from gateway.proxy.infrastructure.catalog_provider_resolver import CatalogProviderResolver
from gateway.proxy.infrastructure.provider_aware_upstream import ProviderAwareCompletionUpstream

pytestmark = pytest.mark.asyncio

_EU_PROFILE_MODEL = "eu.anthropic.claude-3-5-sonnet-20241022-v2:0"


class _FakeAdapter:
    """Records every payload handed to complete()/stream() — a dial-escape counter
    without any real HTTP, isolating this test to the dispatch-selection seam.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append(payload)
        return (200, {"adapter": self.name})

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls.append(payload)

        async def _gen() -> AsyncIterator[bytes]:
            yield b"x"

        return _gen()


async def _dispatch_with_catalog(catalog: dict[str, str]) -> tuple[
    ProviderAwareCompletionUpstream, _FakeAdapter, _FakeAdapter
]:
    async def loader() -> dict[str, str]:
        return dict(catalog)

    resolver = CatalogProviderResolver(loader=loader)
    await resolver.refresh()

    bedrock = _FakeAdapter("bedrock")
    openrouter = _FakeAdapter("openrouter")
    dispatch = ProviderAwareCompletionUpstream(
        adapters={"bedrock": bedrock, "openrouter": openrouter},
        resolver=resolver,
        default_provider="openrouter",
    )
    return dispatch, bedrock, openrouter


async def test_control_exact_case_match_reaches_bedrock_adapter() -> None:
    """Harness sanity check: the correctly-cased catalog id DOES route to bedrock."""
    dispatch, bedrock, openrouter = await _dispatch_with_catalog({_EU_PROFILE_MODEL: "bedrock"})
    status, body = await dispatch.complete({"model": _EU_PROFILE_MODEL, "messages": []})
    assert body["adapter"] == "bedrock"
    assert len(bedrock.calls) == 1
    assert len(openrouter.calls) == 0


async def test_uppercase_prefix_variant_never_reaches_bedrock_adapter() -> None:
    """'EU.anthropic...' (uppercase geo prefix) is NOT the catalog key
    ('eu.anthropic...') — the catalog's exact-match dict lookup (case-sensitive
    Python dict) misses, falls to the default adapter. Proves the helper's
    case-sensitivity gap is not reachable end-to-end via this dispatch seam.
    """
    dispatch, bedrock, openrouter = await _dispatch_with_catalog({_EU_PROFILE_MODEL: "bedrock"})
    mutated = "EU.anthropic.claude-3-5-sonnet-20241022-v2:0"
    status, body = await dispatch.complete({"model": mutated, "messages": []})
    assert len(bedrock.calls) == 0, "LEAK: uppercase-cased id reached the Bedrock adapter"
    assert body["adapter"] == "openrouter"


async def test_leading_whitespace_variant_never_reaches_bedrock_adapter() -> None:
    dispatch, bedrock, openrouter = await _dispatch_with_catalog({_EU_PROFILE_MODEL: "bedrock"})
    mutated = " " + _EU_PROFILE_MODEL
    status, body = await dispatch.complete({"model": mutated, "messages": []})
    assert len(bedrock.calls) == 0, "LEAK: whitespace-mutated id reached the Bedrock adapter"


async def test_unicode_confusable_prefix_never_reaches_bedrock_adapter() -> None:
    """Cyrillic 'е' (U+0435) substituted for Latin 'e' in the 'eu.' prefix —
    visually identical, byte-distinct. Same catalog-gate defense applies.
    """
    dispatch, bedrock, openrouter = await _dispatch_with_catalog({_EU_PROFILE_MODEL: "bedrock"})
    confusable = "еu.anthropic.claude-3-5-sonnet-20241022-v2:0"
    status, body = await dispatch.complete({"model": confusable, "messages": []})
    assert len(bedrock.calls) == 0, "LEAK: unicode-confusable id reached the Bedrock adapter"


async def test_stream_dispatch_also_gates_on_exact_case_match() -> None:
    """The stream() dispatch path re-resolves the provider from the SAME payload
    inside its own inner generator (provider_aware_upstream.py) — confirm it is
    equally gated, not just the complete() path.
    """
    dispatch, bedrock, openrouter = await _dispatch_with_catalog({_EU_PROFILE_MODEL: "bedrock"})
    mutated = "EU.anthropic.claude-3-5-sonnet-20241022-v2:0"
    gen = dispatch.stream({"model": mutated, "messages": []})
    chunks = [chunk async for chunk in gen]
    assert chunks == [b"x"]
    assert len(bedrock.calls) == 0
    assert len(openrouter.calls) == 1


# ---------------------------------------------------------------------------
# "Drop the prefix to escape" — is an unprefixed/direct-invoke Bedrock model id
# defended by a DIFFERENT layer for a geo-pinned tenant? residency-policy's
# shared region_satisfies_pin predicate (Tier 1 existence + Tier 2 router
# filter) is the layer that would catch it — verify its fail-closed semantics
# directly, independent of the bedrock-region-guard task's own contract (which
# deliberately leaves unprefixed ids unconstrained, M3).
# ---------------------------------------------------------------------------


def test_residency_policy_fails_closed_on_null_or_global_region_for_a_pinned_tenant() -> None:
    """M6 (residency-policy, frozen): a candidate whose catalog region is
    NULL/unset or 'global' NEVER satisfies a specific pin — so an unprefixed
    Bedrock model with no catalog region tag is REJECTED for a pinned tenant
    by this layer, not silently allowed through.
    """
    assert region_satisfies_pin("eu", None) is False
    assert region_satisfies_pin("eu", "global") is False


def test_residency_policy_rejects_cross_geo_catalog_region_for_a_pinned_tenant() -> None:
    assert region_satisfies_pin("eu", "us") is False
    assert region_satisfies_pin("eu", "eu") is True


def test_residency_policy_is_a_noop_for_an_unpinned_tenant() -> None:
    """No pin -> every candidate is eligible (byte-identical pre-residency-policy
    behavior) — confirms this layer does not itself constrain unpinned tenants,
    consistent with bedrock-region-guard's own unprefixed-model M3 stance.
    """
    assert region_satisfies_pin(None, "us") is True
    assert region_satisfies_pin(None, None) is True
