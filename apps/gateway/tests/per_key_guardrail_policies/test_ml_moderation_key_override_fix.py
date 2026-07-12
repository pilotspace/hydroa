"""Fix-verification suite for the ml_moderation-drop finding (FIX-TEAM dispatch,
2026-07-10) against `apps/gateway/tests/per_key_guardrail_policies/test_ml_moderation_gap_repro.py`.

That repro test is a BUG-DEMONSTRATION test: its assertions encode the buggy
behavior verbatim ("ml_moderation not in body", "ml_moderation not in stored")
and therefore PASSED before this fix (proving the drop) and now correctly FAILS
after this fix (the drop no longer happens) — flipping it to fail is the intended
outcome of a real fix, not a regression, and per the fix-team hard rule the repro
test itself is never edited to force it back to a false "green".

This file is the companion that asserts the CORRECT, fixed behavior going
forward: `ml_moderation` in a PUT body is now persisted into the key's
guardrail_policy override, echoed back in the response and a subsequent GET
(alongside prompt_injection/pii_mask, handled the same wholesale way), included
in the PUT audit metadata, and correctly partial-merge-preserved/removed like
the other two guardrail fields.
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
from .test_per_key_guardrail_policies import (
    _drain_fire_and_forget,
    _fetch_one_audit_row,
    _key_row,
)

ML_MODERATION_BLOCK = {"enabled": True, "mode": "block", "failure_mode": "fail_closed"}


async def test_put_persists_and_echoes_ml_moderation(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PUT with ml_moderation in the body -> 200, persisted into the key override,
    echoed back in the PUT response, and visible on a subsequent GET — closing the
    silent-drop gap the ml_moderation_gap_repro test caught."""
    jwt, _tenant_id = await signup_and_login(client, tenant_name="MlFixCo", email="owner@mlfix.io")
    key_info = await create_key(client, jwt, name="mlfix-key")

    await set_tenant_guardrails(
        client,
        jwt,
        {"ml_moderation": {"enabled": True, "mode": "block", "failure_mode": "fail_closed"}},
    )

    put_resp = await client.put(
        key_guardrails_path(key_info["key_id"]),
        json={
            "prompt_injection": {"enabled": True, "mode": "block"},
            "ml_moderation": ML_MODERATION_BLOCK,
        },
        headers=auth_jwt(jwt),
    )
    assert put_resp.status_code == 200, f"PUT key guardrails failed: {put_resp.text}"
    body: dict[str, Any] = put_resp.json()

    assert body.get("ml_moderation") == ML_MODERATION_BLOCK, (
        f"ml_moderation must be echoed back in the PUT response; got body={body!r}"
    )
    assert body.get("prompt_injection") == {"enabled": True, "mode": "block"}
    assert body.get("source") == "key"

    row = await _key_row(db_session, key_info["key_id"])
    stored = row[0]
    assert stored is not None
    assert stored.get("ml_moderation") == ML_MODERATION_BLOCK, (
        f"ml_moderation must be persisted into the key's guardrail_policy override; "
        f"stored={stored!r}"
    )
    assert stored.get("prompt_injection") == {"enabled": True, "mode": "block"}

    get_resp = await client.get(key_guardrails_path(key_info["key_id"]), headers=auth_jwt(jwt))
    assert get_resp.status_code == 200, f"GET failed: {get_resp.text}"
    get_body = get_resp.json()
    assert get_body.get("ml_moderation") == ML_MODERATION_BLOCK, (
        f"a subsequent GET must also surface the persisted ml_moderation override; "
        f"got body={get_body!r}"
    )
    assert get_body.get("source") == "key"


async def test_put_ml_moderation_partial_merge_preserved_and_removed(
    client: httpx.AsyncClient,
) -> None:
    """ml_moderation follows the SAME partial-merge-within-the-key-override
    semantics as prompt_injection/pii_mask (M5): a later PUT that omits
    ml_moderation preserves it; an explicit PUT {ml_moderation: null} removes
    just that guardrail, leaving the others untouched."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="MlMergeCo", email="owner@mlmerge.io"
    )
    key_info = await create_key(client, jwt, name="mlmerge-key")

    first = await client.put(
        key_guardrails_path(key_info["key_id"]),
        json={"ml_moderation": ML_MODERATION_BLOCK},
        headers=auth_jwt(jwt),
    )
    assert first.status_code == 200, f"first PUT failed: {first.text}"

    # Second PUT sets pii_mask, omits ml_moderation entirely -> preserved.
    second = await client.put(
        key_guardrails_path(key_info["key_id"]),
        json={"pii_mask": {"enabled": True, "mode": "mask"}},
        headers=auth_jwt(jwt),
    )
    assert second.status_code == 200, f"second PUT failed: {second.text}"
    second_body = second.json()
    assert second_body.get("ml_moderation") == ML_MODERATION_BLOCK, (
        "ml_moderation absent from the second PUT body must be PRESERVED, not dropped"
    )
    assert second_body.get("pii_mask") == {"enabled": True, "mode": "mask"}

    # Third PUT explicitly nulls ml_moderation -> removed; pii_mask untouched.
    third = await client.put(
        key_guardrails_path(key_info["key_id"]),
        json={"ml_moderation": None},
        headers=auth_jwt(jwt),
    )
    assert third.status_code == 200, f"third PUT failed: {third.text}"
    third_body = third.json()
    assert third_body.get("ml_moderation") is None
    assert third_body.get("pii_mask") == {"enabled": True, "mode": "mask"}, (
        "pii_mask must survive an unrelated field's removal"
    )


async def test_audit_metadata_includes_ml_moderation_flags(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PUT audit metadata (M9: key_id + enabled/mode flags, never pattern/message
    content) now includes ml_moderation's enabled/mode alongside the other
    guardrails — the metadata builder derives its field list from
    GuardrailConfigRequest itself rather than a hand-copied name tuple, so it
    can't silently omit a guardrail type again."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="MlAuditCo", email="owner@mlaudit.io"
    )
    key_info = await create_key(client, jwt, name="mlaudit-key")

    resp = await client.put(
        key_guardrails_path(key_info["key_id"]),
        json={"ml_moderation": ML_MODERATION_BLOCK},
        headers=auth_jwt(jwt),
    )
    assert resp.status_code == 200, f"PUT failed: {resp.text}"

    await _drain_fire_and_forget()
    row = await _fetch_one_audit_row(db_session, action="key_guardrail_policy.put")
    assert row is not None, "expected exactly one key_guardrail_policy.put audit event"
    metadata = row[-1]
    assert metadata.get("key_id") == key_info["key_id"]
    assert metadata.get("ml_moderation") == {"enabled": True, "mode": "block"}, (
        f"audit metadata must record ml_moderation's enabled/mode like the other "
        f"guardrails; got metadata={metadata!r}"
    )
    metadata_str = str(metadata)
    assert "fail_closed" not in metadata_str and "failure_mode" not in metadata_str, (
        "audit metadata must only carry enabled/mode — not the full config"
    )
