"""Tenant model-preset value-object, domain error, and store Protocol.

§3 CONTRACT (tenant-preset-store TASK.md) — FROZEN @ v3.

A "preset" lets a tenant map a stable (preset_name, alias_key) selector pair
to a concrete catalog model id (target_model). Callers resolve through the
selector rather than hard-coding a model id; the target can be repointed by
re-upserting without changing anything upstream of the store.

This module defines ONLY the store layer's domain shape (value object, error,
port). There is NO HTTP admin API, NO ingress rewrite, and NO capability
guard here — those are separate, later tasks (see TASK.md §1 SPECIFY).
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


def parse_preset_selector(model_field: str) -> tuple[str, str] | None:
    """Split a ``model`` field on the FIRST colon into ``(preset_name, alias_key)``.

    No colon present -> ``None`` (a bare model id — existing behavior, unaffected).
    One-or-more colons present -> ``(preset_name, alias_key)``, where ``alias_key``
    may itself contain colons (harmless: a stored ``alias_key`` can never contain
    one, per the write-time validator, so such a selector can never match a row).
    """
    if ":" not in model_field:
        return None
    preset_name, _, alias_key = model_field.partition(":")
    return preset_name, alias_key
