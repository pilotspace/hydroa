"""Shared residency enforcement logic (residency-policy TASK.md §3, FROZEN @ v2).

ONE module backs BOTH enforcement tiers so they can never silently drift apart
(mirrors the existing dual-copy-governance convention already used for the catalog
check between use_cases.py and governance.py):

  Tier 1 — governance-layer EXISTENCE check. Called from BOTH
    use_cases.py::CompletionUseCase._enforce_governance AND
    governance.py::NonChatGovernance.authorize, at the SAME synchronized step
    (immediately after the catalog check). Rejects the whole request when zero
    in-region candidates exist anywhere for the resolved model/alias.

  Tier 2 — router-layer DIAL-CONSTRAINT filter. Called from ALL THREE of
    FallbackModelRouter.complete() / .stream() / .stream_resilient(), mirroring the
    existing limit_gate pre-loop-filter shape exactly. This is what actually stops
    an out-of-region candidate from ever being dialed.

M6 fail-closed semantics (Tin-confirmed at freeze): a candidate whose catalog
`region` is NULL/unset OR "global" NEVER satisfies a tenant's specific pin
(us|eu|ap). `global`/unset candidates remain eligible ONLY for an unpinned tenant.
"""

from __future__ import annotations

import uuid

from gateway.core.error_catalog import RESIDENCY_NO_ELIGIBLE_REGION
from gateway.proxy.domain.ports import ResidencyLookup


def region_satisfies_pin(pin: str | None, region: str | None) -> bool:
    """M6: `global`/unset NEVER satisfies a specific pin; no pin -> always satisfied."""
    if pin is None:
        return True
    if region is None or region == "global":
        return False
    return region == pin


async def check_residency_existence(
    residency: ResidencyLookup | None,
    model_id: str,
    tenant_id: uuid.UUID,
    model_groups: dict[str, list[str]] | None,
) -> None:
    """Tier 1 — governance-layer existence check (shared by use_cases.py + governance.py).

    Alias (model_id is a key in model_groups): >=1 in-region candidate must exist
    anywhere in the group. Plain model id: the id itself must satisfy the pin.

    A no-op (byte-identical) when residency is None (feature unwired) or the tenant
    has no pin. Raises RESIDENCY_NO_ELIGIBLE_REGION.exc() (403) when the eligible
    set is empty — R1/R4, fail-closed, never a silent pass-through.
    """
    if residency is None:
        return
    pin = await residency.tenant_pin(tenant_id)
    if pin is None:
        return
    candidates = model_groups[model_id] if model_groups and model_id in model_groups else [model_id]
    regions = await residency.regions_for(candidates)
    eligible = [c for c in candidates if region_satisfies_pin(pin, regions.get(c))]
    if not eligible:
        raise RESIDENCY_NO_ELIGIBLE_REGION.exc(region=pin, model_id=model_id)


async def filter_candidates_by_region(
    residency: ResidencyLookup | None,
    candidates: list[str],
    tenant_id: uuid.UUID,
) -> tuple[str | None, list[str]]:
    """Tier 2 — router pre-loop filter (mirrors the limit_gate pre-loop-filter shape).

    Returns (pin, filtered_candidates). `pin` is the tenant's raw residency pin —
    None when residency is off or the tenant has no pin, in which case
    filtered_candidates is `candidates` UNCHANGED (byte-identical to
    pre-residency-policy routing). Otherwise filtered_candidates is the in-region
    subset — POSSIBLY EMPTY; the caller (FallbackModelRouter) is responsible for
    raising AllCandidatesOutOfRegionError(alias, pin) on an empty result.
    """
    if residency is None:
        return None, candidates
    pin = await residency.tenant_pin(tenant_id)
    if pin is None:
        return None, candidates
    regions = await residency.regions_for(candidates)
    return pin, [c for c in candidates if region_satisfies_pin(pin, regions.get(c))]


async def cache_hit_region_ok(
    residency: ResidencyLookup | None,
    served_model_id: str,
    tenant_id: uuid.UUID,
) -> bool:
    """M7 — re-validate a cache hit's stamped served region against the tenant's
    CURRENT residency pin. True = safe to serve; False = degrade to a MISS (never
    replay a body whose origin region cannot be currently verified as compliant).
    """
    if residency is None:
        return True
    pin = await residency.tenant_pin(tenant_id)
    if pin is None:
        return True
    regions = await residency.regions_for([served_model_id])
    return region_satisfies_pin(pin, regions.get(served_model_id))


__all__ = [
    "cache_hit_region_ok",
    "check_residency_existence",
    "filter_candidates_by_region",
    "region_satisfies_pin",
]
