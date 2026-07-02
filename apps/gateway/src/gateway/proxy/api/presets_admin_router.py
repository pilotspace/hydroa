"""Admin API for tenant-scoped model presets — the FIRST admin-write path for this table.

Routes (OWNER-only, prefix /admin/presets):
  GET    /admin/presets                          — list all presets for the caller tenant
  PUT    /admin/presets/{preset_name}/{alias_key} — create-or-replace (upsert) a preset
  DELETE /admin/presets/{preset_name}/{alias_key} — remove a preset; ALWAYS 204 (idempotent)

§3 CONTRACT (preset-admin-surface TASK.md) — FROZEN @ v2.

SECURITY INVARIANTS:
  - OWNER-only: tenant_id comes ONLY from the verified JWT via
    ``_require_owner_tenant_id`` (imported from ``provider_keys_admin_router``, the v25
    BYOK admin-API precedent, NOT re-implemented here) — never a client-supplied
    body/query/path value, so cross-tenant access is architecturally impossible.
  - A ``preset_name``/``alias_key`` path segment containing "/" is rejected with
    400 ``ERR_PRESET_SELECTOR_INVALID`` BEFORE the store is ever called, on BOTH PUT
    and DELETE (see ``_reject_slash``). This task is the first-ever writer for this
    table (no admin-write path existed before), so no preset row can ever be created
    with "/" in it going forward — making the ``{preset_name}/{alias_key}`` path-param
    route safe by construction, without touching the already-shipped
    ``tenant-preset-store`` validator (which still technically permits "/" at the
    store/DB level).

NOTE on ``store.upsert`` return shape: ``TenantModelPresetStore.upsert`` returns ``None``
(a fire-and-forget validated write — see ``gateway.proxy.domain.model_presets``), so the
resulting ``TenantModelPreset`` is fetched afterward via ``store.list`` + a linear find,
mirroring ``provider_keys_admin_router._status_for``'s identical list-then-find pattern
for building a full response object (here, to also surface ``updated_at``).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from gateway.core.error_catalog import (
    INTERNAL_ERROR,
    PRESET_SELECTOR_INVALID,
    PRESET_TARGET_UNKNOWN,
)
from gateway.proxy.api.provider_keys_admin_router import (
    _require_owner_tenant_id,  # pyright: ignore[reportPrivateUsage]
)
from gateway.proxy.domain.model_presets import ModelPresetError, TenantModelPreset

presets_admin_router = APIRouter(prefix="/admin/presets", tags=["presets-admin"])


class PresetUpsertBody(BaseModel):
    """PUT body — ``preset_name``/``alias_key`` come from the path, not the body."""

    target_model: str


def _reject_slash(value: str) -> None:
    """Reject a preset_name/alias_key segment containing "/" — 400, before any store call.

    Router-local guard (NEW, additive) — keeps the ``{preset_name}/{alias_key}`` path-param
    route unambiguous regardless of when/whether the ASGI stack decodes a percent-encoded
    "/" relative to route-segment matching. See module docstring.
    """
    if "/" in value:
        raise PRESET_SELECTOR_INVALID.exc() from None


async def _preset_for(
    request: Request, tenant_id: UUID, preset_name: str, alias_key: str
) -> TenantModelPreset | None:
    """Return the TenantModelPreset for (preset_name, alias_key), or None.

    ``store.upsert`` returns ``None`` — this mirrors
    ``provider_keys_admin_router._status_for``'s list-then-find pattern to build the
    full response object (including ``updated_at``) after a successful write.
    """
    store = request.app.state.tenant_model_preset_store
    presets: list[TenantModelPreset] = await store.list(tenant_id)
    for preset in presets:
        if preset.preset_name == preset_name and preset.alias_key == alias_key:
            return preset
    return None


@presets_admin_router.get("")
async def list_presets(request: Request) -> dict[str, list[TenantModelPreset]]:
    """List the caller-tenant's presets ([] when none)."""
    tenant_id = _require_owner_tenant_id(request)
    store = request.app.state.tenant_model_preset_store
    presets: list[TenantModelPreset] = await store.list(tenant_id)
    return {"presets": presets}


@presets_admin_router.put("/{preset_name}/{alias_key}")
async def upsert_preset(
    preset_name: str, alias_key: str, body: PresetUpsertBody, request: Request
) -> TenantModelPreset:
    """Create or replace (upsert) the caller-tenant's preset for (preset_name, alias_key)."""
    tenant_id = _require_owner_tenant_id(request)
    _reject_slash(preset_name)
    _reject_slash(alias_key)

    store = request.app.state.tenant_model_preset_store
    try:
        await store.upsert(tenant_id, preset_name, alias_key, body.target_model)
    except ModelPresetError as exc:
        # Map the store's domain errors to RFC-9457 problems instead of a raw 500
        # (design-for-failure on the store IO path) — mirrors put_provider_key's
        # identical ModelPresetError/ProviderCredentialError -> ErrorSpec handling.
        if exc.code == "ERR_PRESET_SELECTOR_INVALID":
            raise PRESET_SELECTOR_INVALID.exc() from None
        if exc.code == "ERR_PRESET_TARGET_UNKNOWN":
            raise PRESET_TARGET_UNKNOWN.exc() from None
        raise INTERNAL_ERROR.exc() from None

    preset = await _preset_for(request, tenant_id, preset_name, alias_key)
    if preset is None:  # pragma: no cover — defensive: a row was just upserted
        raise INTERNAL_ERROR.exc()
    return preset


@presets_admin_router.delete("/{preset_name}/{alias_key}", status_code=204)
async def delete_preset(preset_name: str, alias_key: str, request: Request) -> Response:
    """Delete the caller-tenant's preset; ALWAYS 204 (idempotent — no existence check surfaced)."""
    tenant_id = _require_owner_tenant_id(request)
    _reject_slash(preset_name)
    _reject_slash(alias_key)

    store = request.app.state.tenant_model_preset_store
    await store.delete(tenant_id, preset_name, alias_key)
    return Response(status_code=204)
