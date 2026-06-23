"""Merge a persisted routing config OVER the env-derived Settings (v32 routing-config-store §3).

DB-wins-when-present, env fallback. Validation parity is achieved by re-running the SAME
Settings/Deployment validators: a probe Settings is constructed from the env dump with the
stored routing overrides applied (this raises the existing ValueError on an invalid config —
UNKNOWN_ROUTING_STRATEGY, DUPLICATE_DEPLOYMENT, …). The VALIDATED routing fields are then
applied onto the real settings via model_copy, so non-routing fields (secrets, db url) are
preserved untouched (the probe's masked secrets are discarded).

All routing validators are self-contained to routing fields (deployments / routing_strategy /
loadbal_*), so validating them in isolation on the probe is sufficient — there is no
routing-vs-non-routing cross-field validator.
"""

from __future__ import annotations

from typing import Any

from gateway.core.config import Settings

# The overridable scalar routing fields (deployments handled separately via the model_groups alias).
# Public: also consumed by the routing-config-write endpoint to bound the persistable key set.
ROUTING_OVERRIDE_KEYS = (
    "routing_strategy",
    "cooldown_failure_threshold",
    "cooldown_ttl_s",
    "cooldown_window_s",
    "upstream_max_retries",
    "upstream_retry_backoff_base_s",
    "upstream_retry_deadline_s",
    "upstream_fallback_on_error",
    "upstream_stream_resilience_enabled",
    "loadbal_ewma_alpha",
    "loadbal_inflight_ttl_s",
)


def merge_routing_config(settings: Settings, stored: dict[str, Any] | None) -> Settings:
    """Return settings with the stored routing overrides applied (DB-wins), or unchanged if None.

    Raises ValueError (the existing Settings/Deployment validator error) if the stored config is
    invalid — the caller (boot) catches it and falls back to the env config.
    """
    if stored is None:
        return settings

    # 1. Validate via a probe Settings built from the env dump + stored overrides.
    #    `deployments` carries a validation_alias of `model_groups`; reconstruct via that kwarg.
    base = settings.model_dump()
    deployments_dump = base.pop("deployments", {})
    model_groups = stored.get("model_groups", deployments_dump)
    scalar_overrides = {k: stored[k] for k in ROUTING_OVERRIDE_KEYS if k in stored}
    probe = Settings(**{**base, **scalar_overrides, "model_groups": model_groups})

    # 2. Apply only the VALIDATED routing fields onto the real settings (preserves secrets/db).
    update: dict[str, Any] = {k: getattr(probe, k) for k in ROUTING_OVERRIDE_KEYS}
    update["deployments"] = probe.deployments
    return settings.model_copy(update=update)
