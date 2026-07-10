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


async def test_ml_moderation_silently_dropped_from_key_override(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PUT with ml_moderation in the body -> 200, but the field is never persisted
    and never echoed back — silent data loss on a security-relevant guardrail."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="MlGapCo", email="owner@mlgap.io"
    )
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

    # FINDING: response has no ml_moderation field at all (KeyGuardrailPolicyResponse
    # does not declare one) -- the caller gets zero signal that it was dropped.
    assert "ml_moderation" not in body, (
        f"expected ml_moderation to be silently absent from the response (current "
        f"buggy behavior); got body={body!r}"
    )

    # FINDING: the stored guardrail_policy column never received ml_moderation.
    row = await _key_row(db_session, key_info["key_id"])
    stored = row[0]
    assert stored is not None
    assert "ml_moderation" not in stored, (
        f"expected ml_moderation to be silently dropped from the persisted key "
        f"override (current buggy behavior); stored={stored!r}"
    )

    # CONSEQUENCE: because resolution is wholesale (key override entirely replaces
    # tenant config), this key now has ZERO ml_moderation coverage even though the
    # tenant has it ON in fail_closed mode, and there is no way via this API to set
    # it back on the key.
    assert stored.get("prompt_injection") == {"enabled": True, "mode": "block"}
