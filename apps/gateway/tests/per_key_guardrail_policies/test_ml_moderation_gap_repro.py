"""VERIFY repro (adversarial finding, appsec lens): the key-level guardrail router
reuses `GuardrailConfigRequest` VERBATIM from tenants/api/guardrail_router.py, which
includes an `ml_moderation` field (MlModerationConfig) — but the new PUT handler
(key_guardrail_router.py:put_key_guardrails) only branches on "prompt_injection" and
"pii_mask" in `body.model_fields_set`. `ml_moderation` is silently accepted by
Pydantic and then silently DROPPED — never merged into the stored override, never
returned in the response (`KeyGuardrailPolicyResponse` has no `ml_moderation` field
at all).

Because resolution is WHOLESALE (M1: a non-NULL key override replaces the tenant
config entirely, no per-field merge), setting ANY key-level override — even one that
only intends to add prompt_injection or pii_mask — silently and irreversibly strips
ml_moderation content-moderation coverage for that key. There is no way to set
ml_moderation on a key via this endpoint, and the caller gets no error and no
indication in the response that it was dropped.

Not in TASK.md §0 GROUND (which enumerates only PromptInjectionConfig/PiiMaskConfig/
CustomPatternItem — MlModerationConfig did not exist at this task's Ground SHA) — a
genuine cross-feature integration gap versus the sibling ml-moderation-layer task
that landed ml_moderation into the SAME reused Pydantic model and the SAME
`guardrail_configs` dict the proxy evaluator reads (see
proxy/infrastructure/ml_moderation_evaluator.py:172 `cfg = guardrail_configs.get("ml_moderation")`).
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    auth_jwt,
    create_key,
    key_guardrails_path,
    set_tenant_guardrails,
    signup_and_login,
)
from .test_per_key_guardrail_policies import _key_row


async def test_ml_moderation_carried_into_key_override(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PUT with ml_moderation in the body -> 200, and the field is persisted AND
    echoed back — no silent data loss on a security-relevant guardrail. (Was a
    verify-wave repro asserting the buggy drop; inverted at fix-integration to lock
    in the corrected behavior the fix delivers — see companion
    test_ml_moderation_key_override_fix.py.)"""
    jwt, _tenant_id = await signup_and_login(client, tenant_name="MlGapCo", email="owner@mlgap.io")
    key_info = await create_key(client, jwt, name="mlgap-key")

    # Tenant has ml_moderation ON (block mode) as a compliance-critical control.
    await set_tenant_guardrails(
        client,
        jwt,
        {"ml_moderation": {"enabled": True, "mode": "block", "failure_mode": "fail_closed"}},
    )

    # Admin explicitly TRIES to carry ml_moderation into the key override, alongside
    # a prompt_injection override.
    put_resp = await client.put(
        key_guardrails_path(key_info["key_id"]),
        json={
            "prompt_injection": {"enabled": True, "mode": "block"},
            "ml_moderation": {"enabled": True, "mode": "block", "failure_mode": "fail_closed"},
        },
        headers=auth_jwt(jwt),
    )
    assert put_resp.status_code == 200, f"PUT key guardrails failed: {put_resp.text}"
    body: dict[str, Any] = put_resp.json()

    # FIXED: the response now echoes ml_moderation, so the caller gets a truthful
    # round-trip (KeyGuardrailPolicyResponse declares the field).
    assert body.get("ml_moderation") == {
        "enabled": True,
        "mode": "block",
        "failure_mode": "fail_closed",
    }, f"expected ml_moderation echoed in the response; got body={body!r}"

    # FIXED: the stored guardrail_policy column now carries ml_moderation.
    row = await _key_row(db_session, key_info["key_id"])
    stored = row[0]
    assert stored is not None
    assert stored.get("ml_moderation") == {
        "enabled": True,
        "mode": "block",
        "failure_mode": "fail_closed",
    }, f"expected ml_moderation persisted on the key override; stored={stored!r}"

    # CONSEQUENCE (now correct): resolution is wholesale (key override entirely
    # replaces tenant config), but the key now carries its OWN ml_moderation, so
    # coverage is preserved rather than silently stripped.
    assert stored.get("prompt_injection") == {"enabled": True, "mode": "block"}
