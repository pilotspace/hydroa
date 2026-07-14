"""Red/green regression suite for the MED key-level guardrail floor finding
(audit-remediation package A1, finding 3): `PUT /admin/keys/{key_id}/guardrails`
wholesale-replaces the tenant's guardrail policy for that key with no floor —
so ANY key-level override, even one that never mentions a tenant-mandated
guardrail at all, silently and irreversibly disabled that guardrail's coverage
for the key (resolution at auth time reads ONE OF the key/tenant rows in full,
wholesale, no per-field merge — see `key_guardrail_router.py`'s module
docstring). A malicious or careless admin (or a compromised owner/admin
credential) could use a per-key override to quietly strip a compliance-
mandated PII mask or prompt-injection block for one specific key.

Fix: `_apply_tenant_floor` bakes the tenant's mandated guardrail floor into
the STORED key override at PUT time — a key may tighten (stricter mode, or
simply leaving a guardrail enabled) but the system enforces that it can never
disable or downgrade a tenant-mandated guardrail below the tenant's own mode.

HTTP-level suite (real Postgres, httpx.ASGITransport) — reuses the shared
per_key_guardrail_policies arrange helpers (signup_and_login / create_key /
set_tenant_guardrails / key_guardrails_path) rather than duplicating ~40 lines
of signup/create-key plumbing.
"""

from __future__ import annotations

from typing import Any

import httpx

from gateway.keys.infrastructure.repository import apply_tenant_guardrail_floor

from tests.per_key_guardrail_policies.conftest import (
    auth_jwt,
    create_key,
    key_guardrails_path,
    set_tenant_guardrails,
    signup_and_login,
)


# ---------------------------------------------------------------------------
# Finding 3a — a key override that never mentions the tenant-mandated
# guardrail must NOT silently disable it (the core wholesale-replace bug).
# ---------------------------------------------------------------------------


async def test_key_override_cannot_silently_drop_tenant_mandated_pii_mask(
    client: httpx.AsyncClient,
) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="FloorCo1", email="owner@floor1.io"
    )
    key_info = await create_key(client, jwt, name="floor-key-1")

    # Tenant mandates pii_mask in "mask" mode — a compliance control.
    await set_tenant_guardrails(
        client, jwt, {"pii_mask": {"enabled": True, "mode": "mask"}}
    )

    # Admin sets a key-level override that ONLY touches prompt_injection —
    # never mentions pii_mask at all.
    put_resp = await client.put(
        key_guardrails_path(key_info["key_id"]),
        json={"prompt_injection": {"enabled": True, "mode": "block"}},
        headers=auth_jwt(jwt),
    )
    assert put_resp.status_code == 200, f"PUT failed: {put_resp.text}"
    body: dict[str, Any] = put_resp.json()

    assert body.get("pii_mask") == {"enabled": True, "mode": "mask"}, (
        f"a key override that never mentions pii_mask must NOT silently drop "
        f"the tenant's mandated coverage; got body={body!r}"
    )
    assert body.get("prompt_injection") == {"enabled": True, "mode": "block"}
    assert body.get("source") == "key"

    # A subsequent GET (the actual resolved config a request would use) must
    # also reflect the floor, not just the PUT response.
    get_resp = await client.get(key_guardrails_path(key_info["key_id"]), headers=auth_jwt(jwt))
    assert get_resp.status_code == 200
    assert get_resp.json().get("pii_mask") == {"enabled": True, "mode": "mask"}


# ---------------------------------------------------------------------------
# Finding 3b — a key override that EXPLICITLY tries to disable a
# tenant-mandated guardrail must be clamped back to the tenant floor.
# ---------------------------------------------------------------------------


async def test_key_override_cannot_explicitly_disable_tenant_mandated_guardrail(
    client: httpx.AsyncClient,
) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="FloorCo2", email="owner@floor2.io"
    )
    key_info = await create_key(client, jwt, name="floor-key-2")

    await set_tenant_guardrails(
        client, jwt, {"prompt_injection": {"enabled": True, "mode": "block"}}
    )

    # Admin explicitly tries to turn prompt_injection OFF for this one key.
    put_resp = await client.put(
        key_guardrails_path(key_info["key_id"]),
        json={"prompt_injection": {"enabled": False, "mode": "audit"}},
        headers=auth_jwt(jwt),
    )
    assert put_resp.status_code == 200, f"PUT failed: {put_resp.text}"
    body = put_resp.json()

    assert body.get("prompt_injection") == {"enabled": True, "mode": "block"}, (
        f"an explicit attempt to disable a tenant-mandated guardrail on a key "
        f"must be clamped back to the tenant floor, not honored; got body={body!r}"
    )


# ---------------------------------------------------------------------------
# Finding 3c — a key may TIGHTEN a tenant-mandated guardrail beyond the floor.
# ---------------------------------------------------------------------------


async def test_key_override_may_tighten_beyond_tenant_mandated_mode(
    client: httpx.AsyncClient,
) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="FloorCo3", email="owner@floor3.io"
    )
    key_info = await create_key(client, jwt, name="floor-key-3")

    # Tenant only mandates the weak (audit) mode for pii_mask.
    await set_tenant_guardrails(client, jwt, {"pii_mask": {"enabled": True, "mode": "audit"}})

    # Key tightens it to the strong (mask) mode.
    put_resp = await client.put(
        key_guardrails_path(key_info["key_id"]),
        json={"pii_mask": {"enabled": True, "mode": "mask"}},
        headers=auth_jwt(jwt),
    )
    assert put_resp.status_code == 200, f"PUT failed: {put_resp.text}"
    body = put_resp.json()

    assert body.get("pii_mask") == {"enabled": True, "mode": "mask"}, (
        f"a key must be free to TIGHTEN a tenant-mandated guardrail beyond the "
        f"tenant's own mode; got body={body!r}"
    )


# ---------------------------------------------------------------------------
# Finding 3d — a key may downgrade/disable a guardrail the TENANT never
# mandated in the first place (no floor to enforce when tenant doesn't
# mandate it — must not become impossible to configure a key-only guardrail).
# ---------------------------------------------------------------------------


async def test_key_override_free_when_tenant_does_not_mandate_guardrail(
    client: httpx.AsyncClient,
) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="FloorCo4", email="owner@floor4.io"
    )
    key_info = await create_key(client, jwt, name="floor-key-4")

    # Tenant has NO guardrails configured at all.
    put_resp = await client.put(
        key_guardrails_path(key_info["key_id"]),
        json={"pii_mask": {"enabled": True, "mode": "audit"}},
        headers=auth_jwt(jwt),
    )
    assert put_resp.status_code == 200, f"PUT failed: {put_resp.text}"
    body = put_resp.json()

    assert body.get("pii_mask") == {"enabled": True, "mode": "audit"}, (
        f"a key-only guardrail (tenant never mandated it) must be honored "
        f"exactly as the admin configured it; got body={body!r}"
    )


# ---------------------------------------------------------------------------
# HOLE 3b (adversarial-verification follow-up) — the floor must ALSO clamp
# `failure_mode`, not just `mode`: a key may not weaken a tenant-mandated
# fail_closed down to fail_open while keeping mode=block (still "enabled and
# strict-mode", but silently degrading how a moderation-call FAILURE is
# handled — the opposite of what the tenant mandated).
# ---------------------------------------------------------------------------


async def test_key_override_cannot_weaken_ml_moderation_failure_mode(
    client: httpx.AsyncClient,
) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="FloorCo5", email="owner@floor5.io"
    )
    key_info = await create_key(client, jwt, name="floor-key-5")

    await set_tenant_guardrails(
        client,
        jwt,
        {"ml_moderation": {"enabled": True, "mode": "block", "failure_mode": "fail_closed"}},
    )

    # Key keeps mode=block (matches the floor) but tries to weaken failure_mode
    # to fail_open -- must be clamped back to fail_closed.
    put_resp = await client.put(
        key_guardrails_path(key_info["key_id"]),
        json={
            "ml_moderation": {"enabled": True, "mode": "block", "failure_mode": "fail_open"}
        },
        headers=auth_jwt(jwt),
    )
    assert put_resp.status_code == 200, f"PUT failed: {put_resp.text}"
    body = put_resp.json()

    assert body.get("ml_moderation") == {
        "enabled": True,
        "mode": "block",
        "failure_mode": "fail_closed",
    }, (
        f"a key may not weaken a tenant-mandated failure_mode=fail_closed to "
        f"fail_open; got body={body!r}"
    )


# ---------------------------------------------------------------------------
# HOLE 3a (adversarial-verification follow-up) — unit-level coverage of the
# SHARED floor function used both at PUT-time (key_guardrail_router.py) and
# at auth-resolution time (keys/infrastructure/repository.py).
# ---------------------------------------------------------------------------


def test_apply_tenant_guardrail_floor_backfills_missing_and_clamps_mode_and_failure_mode() -> (
    None
):
    tenant_configs = {
        "pii_mask": {"enabled": True, "mode": "mask"},
        "ml_moderation": {"enabled": True, "mode": "block", "failure_mode": "fail_closed"},
    }
    # Key override: never mentions pii_mask at all; ml_moderation present but
    # weaker on both axes (audit mode not applicable here -- mode already
    # matches "block", but failure_mode is weakened to fail_open).
    effective = {
        "prompt_injection": {"enabled": True, "mode": "block"},
        "ml_moderation": {"enabled": True, "mode": "block", "failure_mode": "fail_open"},
    }

    floored = apply_tenant_guardrail_floor(effective, tenant_configs)

    assert floored["pii_mask"] == {"enabled": True, "mode": "mask"}, (
        "a field entirely absent from the effective config must be backfilled "
        "from the tenant floor"
    )
    assert floored["ml_moderation"]["failure_mode"] == "fail_closed", (
        "failure_mode must be clamped up to the tenant's mandate, not just mode"
    )
    assert floored["ml_moderation"]["mode"] == "block"
    assert floored["prompt_injection"] == {"enabled": True, "mode": "block"}, (
        "a field the tenant does not mandate must pass through untouched"
    )


def test_apply_tenant_guardrail_floor_is_noop_when_key_already_meets_floor() -> None:
    tenant_configs = {"pii_mask": {"enabled": True, "mode": "audit"}}
    effective = {"pii_mask": {"enabled": True, "mode": "mask"}}

    floored = apply_tenant_guardrail_floor(effective, tenant_configs)

    assert floored["pii_mask"] == {"enabled": True, "mode": "mask"}, (
        "a key already at/above the floor must be left untouched (tightening allowed)"
    )
