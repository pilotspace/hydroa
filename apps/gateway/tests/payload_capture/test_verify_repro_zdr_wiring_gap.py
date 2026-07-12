"""VERIFY repro (add-verify, 2026-07-10): the REAL ZDR signal (tenants.zdr_enabled,
already shipped by the sibling tenant-retention-zdr task, integration-merged in this
same wave) is never consulted by payload-capture-store's ZdrOverridePort wiring.

gateway/tenants/application/retention_policy.py:is_zdr(session, tenant_id) is
explicitly documented as "Published read port for the payload-capture-store sibling
task to consume ... its shape is pinned" — but gateway/main.py:1132 still wires
`app.state.zdr_port = AlwaysAllowCapture()` (the permissive no-op, always False),
and nothing anywhere adapts the real tenants.zdr_enabled column into the
ZdrOverridePort Protocol. This means the entire ZDR fail-closed contract for payload
capture (§1 Must, §2 scenarios "ZDR tenant never gets a payload row") is DEAD CODE in
production for any tenant that has genuinely enabled ZDR via the real feature: the
PUT /admin/capture {"enabled": true} 409-reject never fires, and the live per-request
suppression in SqlAlchemyPayloadCapture.capture() never fires either — both silently
treat every ZDR tenant as non-ZDR.

Minimal, does not touch any frozen test or contract.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.payload_capture.test_payload_capture_store import (
    ADMIN_CAPTURE,
    COMPLETIONS,
    FakeCompletionUpstream,
    _request_log_rows,
    auth_key,
    completion_payload,
    create_key,
    signup_and_login,
)


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    model_id = "openai/gpt-4o-mini"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true) ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o-mini"},
    )
    snap_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:sid, :mid, :p, :c, now()) ON CONFLICT DO NOTHING"
        ),
        {"sid": str(snap_id), "mid": model_id, "p": "0.000001", "c": "0.000002"},
    )
    await db_session.commit()
    return model_id


async def test_repro_real_zdr_tenant_via_default_wiring_gets_capture_row(
    client: httpx.AsyncClient,
    app: object,
    active_model: str,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="RealZdrCo", email="owner@realzdr.io"
    )
    key_info = await create_key(client, jwt, name="realzdr-key")

    # Flip the REAL (already-shipped) ZDR column directly — this is exactly what the
    # tenant-retention-zdr feature's own admin surface would do; we bypass its router
    # here purely to isolate the wiring gap under test.
    await db_session.execute(
        text("UPDATE tenants SET zdr_enabled = true WHERE id = :tid"),
        {"tid": tenant_id},
    )
    await db_session.commit()

    # 1) The PUT /admin/capture ZDR-block (409 ERR_CAPTURE_ZDR_BLOCKED) is contracted to
    #    fire for a tenant under ZDR. With the app's DEFAULT wiring (app.state.zdr_port
    #    left at its production default, no test override), it does not.
    put_resp = await client.put(
        ADMIN_CAPTURE, json={"enabled": True}, headers={"Authorization": f"Bearer {jwt}"}
    )
    print(
        f"\nPUT /admin/capture on a REAL zdr_enabled=true tenant -> {put_resp.status_code} {put_resp.text}"
    )

    # 2) Even if (1) somehow allowed the toggle, the LIVE per-request suppression inside
    #    SqlAlchemyPayloadCapture.capture() is contracted to suppress every write for this
    #    tenant. With the app's default wiring, it does not either.
    app.state.completion_upstream = FakeCompletionUpstream()  # type: ignore[attr-defined]
    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model),
        headers=auth_key(key_info["key"]),
    )
    assert resp.status_code == 200
    await asyncio.sleep(0.3)

    rows = await _request_log_rows(db_session, tenant_id)
    print(f"request_logs rows for a REAL zdr_enabled=true tenant: {len(rows)}")

    # THE DEFECT: both the 409 toggle-reject AND the live-write-suppression are
    # contracted to hold for zdr_enabled=true; with default production wiring, at least
    # one (in fact both) silently pass through instead.
    assert put_resp.status_code == 409, (
        "REPRO CONFIRMED (toggle): PUT /admin/capture succeeded "
        f"({put_resp.status_code}) for a tenant with the REAL zdr_enabled=true, because "
        "app.state.zdr_port is wired to the permissive AlwaysAllowCapture no-op instead "
        "of the sibling tenant-retention-zdr task's published tenants.zdr_enabled read."
    )
    assert len(rows) == 0, (
        "REPRO CONFIRMED (live write): a request_logs row was written for a tenant with "
        "the REAL zdr_enabled=true — the ZDR fail-closed contract is defeated by the "
        "default production wiring."
    )
