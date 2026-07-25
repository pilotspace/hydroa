"""Tenant model-preset value-object, domain error, and store Protocol.

§3 CONTRACT (tenant-preset-store TASK.md) — FROZEN @ v3.

A "preset" lets a tenant map a stable (preset_name, alias_key) selector pair
to a concrete catalog model id (target_model). Callers resolve through the
selector rather than hard-coding a model id; the target can be repointed by
re-upserting without changing anything upstream of the store.

This module defines ONLY the store layer's domain shape (value object, error,
port). There is NO HTTP admin API, NO ingress rewrite, and NO capability
guard here — those are separate, later tasks (see TASK.md §1 SPECIFY).

HEAL (finetune-model-registry PLAN.md §3, 2026-07-24): ``ft:`` is a RESERVED
provider-native model-id prefix — OpenAI's fine-tuned-model id shape is
``ft:{base}:{org}::{suffix}``, which itself contains colons. ``parse_preset_
selector`` exempts any ``ft:``-prefixed ``model`` field from preset parsing
so a registered fine-tuned model is always resolvable by its real id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

# ---------------------------------------------------------------------------
# TenantModelPreset — immutable value object returned by list()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TenantModelPreset:
    """One (preset_name, alias_key) → target_model mapping for a tenant.

    Returned by ``TenantModelPresetStore.list`` — the tenant_id is the caller's
    filter key and is intentionally NOT repeated on the value object.
    """

    preset_name: str
    alias_key: str
    target_model: str
    updated_at: datetime


# ---------------------------------------------------------------------------
# Domain error
# ---------------------------------------------------------------------------


class ModelPresetError(Exception):
    """Raised by the store for any preset validation failure.

    Carries a stable ``.code`` string that maps to an error_catalog ``ErrorSpec``
    for the later HTTP admin-API task. Mirrors ``ProviderCredentialError``'s
    shape (see ``gateway.proxy.domain.provider_credentials``).
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code: str = code


# ---------------------------------------------------------------------------
# TenantModelPresetStore Protocol port
# ---------------------------------------------------------------------------


@runtime_checkable
class TenantModelPresetStore(Protocol):
    """Port for per-tenant model-preset persistence.

    Implementations:
    - ``DbTenantModelPresetStore`` — PostgreSQL store (validates the target
      model against the catalog via a ``ModelChecker`` before every write).

    All methods are async; ``tenant_id`` filtering always happens in SQL
    (never client-side) so cross-tenant isolation cannot regress.
    """

    async def upsert(
        self,
        tenant_id: UUID,
        preset_name: str,
        alias_key: str,
        target_model: str,
    ) -> None:
        """Persist (or replace) the mapping for ``(tenant_id, preset_name, alias_key)``.

        Raises ``ModelPresetError("ERR_PRESET_SELECTOR_INVALID")`` when
        ``preset_name`` or ``alias_key`` is empty, longer than 64 characters,
        or contains a colon.
        Raises ``ModelPresetError("ERR_PRESET_TARGET_UNKNOWN")`` when
        ``target_model`` is not an active model in the catalog.
        Validation always runs before any write — a rejected upsert never
        leaves a partial row.
        """
        ...

    async def resolve(
        self,
        tenant_id: UUID,
        preset_name: str,
        alias_key: str,
    ) -> str | None:
        """Return the mapped ``target_model``, or ``None`` if no such mapping exists."""
        ...

    async def list(
        self,
        tenant_id: UUID,
    ) -> list[TenantModelPreset]:
        """Return every preset mapping for ``tenant_id``, ordered by (preset_name, alias_key)."""
        ...

    async def delete(
        self,
        tenant_id: UUID,
        preset_name: str,
        alias_key: str,
    ) -> bool:
        """Delete the mapping and return ``True`` iff a row existed."""
        ...


# ---------------------------------------------------------------------------
# parse_preset_selector — pure helper (preset-resolution-ingress TASK.md §3)
# ---------------------------------------------------------------------------


#: Reserved provider-native colon-prefix — HEAL (finetune-model-registry PLAN.md §3,
#: data-sensitive refute-read, 2026-07-24): OpenAI's real fine-tuned-model id is
#: ``ft:{base}:{org}::{suffix}`` — it CONTAINS colons. Before this exemption, ANY
#: colon-bearing ``model`` field (including a bare ft:* id) was split on the FIRST
#: colon into a bogus (preset_name, alias_key) pair, so a caller naming its own
#: registered fine-tuned model by its real id got PRESET_NOT_FOUND instead of the
#: model — a composed feature-purpose break the per-piece test suite never
#: exercised. ``ft:`` is checked BEFORE the colon-split so a bare ft:* id always
#: resolves as a normal catalog model id, never as a preset selector.
#:
#: Sanity-checked (2026-07-24) for OTHER provider-native colon-bearing ids this
#: helper would also misparse: Bedrock's cross-region-inference-profile ids DO
#: contain a colon too (e.g. "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
#: seeded by migration 9cdca76231c6) and are NOT exempted here — that is a
#: pre-existing gap, wider than and unrelated to this HEAL's scope (fine-tuned
#: models), and is NOT fixed by this change. Flagged as a spec delta for a
#: separate task rather than folded into this fix (a prefix-exemption for a
#: single-dot-segment id shape risks masking a genuine ``preset:alias`` typo for
#: unrelated selectors — a real fix needs its own scoped contract).
_RESERVED_MODEL_ID_PREFIXES: tuple[str, ...] = ("ft:",)


def parse_preset_selector(model_field: str) -> tuple[str, str] | None:
    """Split a ``model`` field on the FIRST colon into ``(preset_name, alias_key)``.

    A reserved provider-native colon-prefix (currently only ``ft:`` — OpenAI's
    fine-tuned-model id shape ``ft:{base}:{org}::{suffix}``) -> ``None`` ALWAYS,
    even though it contains a colon: it is a bare model id, never a preset
    selector (finetune-model-registry PLAN.md §3 HEAL).
    No colon present -> ``None`` (a bare model id — existing behavior, unaffected).
    One-or-more colons present (and no reserved prefix matched) -> ``(preset_name,
    alias_key)``, where ``alias_key`` may itself contain colons (harmless: a stored
    ``alias_key`` can never contain one, per the write-time validator, so such a
    selector can never match a row).
    """
    if model_field.startswith(_RESERVED_MODEL_ID_PREFIXES):
        return None
    if ":" not in model_field:
        return None
    preset_name, _, alias_key = model_field.partition(":")
    return preset_name, alias_key
