"""VERIFY repro (add-verify, 2026-07-10): capture writes RAW PII when the tenant has
capture enabled but has NOT separately configured guardrails pii_mask=mask.

This is the codebase's default state for every tenant (TenantRow.guardrail_configs
defaults to '{}'::jsonb — "empty object = no guardrails enabled", per
tenants/infrastructure/orm.py:106). `PUT /admin/capture` and `PUT /admin/guardrails`
are two entirely independent admin toggles; nothing forces the second when the first
is set. `mask_pii_in_messages` (guardrail_evaluator.py:229) short-circuits to a
pass-through no-op whenever pii_mask isn't enabled+mode=mask (line 253), so
capture_writer.py persists the UNTOUCHED raw content while still writing
scrub_status="scrubbed" (the unconditional default at capture_writer.py:137,
never revisited on this path) — a false "scrubbed" label on a 100% raw row.

Minimal, does not touch any frozen test or contract.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.payload_capture.test_payload_capture_store import (
    ADMIN_CAPTURE,
    COMPLETIONS,
    FakeCompletionUpstream,
    UPSTREAM_BODY_WITH_PII,
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


async def test_repro_capture_on_without_guardrails_persists_raw_pii(
    client: httpx.AsyncClient,
    app: object,
    active_model: str,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="RawCaptureCo", email="owner@rawcapture.io"
    )
    key_info = await create_key(client, jwt, name="raw-cap-key")

    # Deliberately do NOT call PUT /admin/guardrails — leave guardrail_configs at its
    # DB default ('{}'::jsonb, "no guardrails enabled").
    put_resp = await client.put(
        ADMIN_CAPTURE, json={"enabled": True}, headers={"Authorization": f"Bearer {jwt}"}
    )
    assert put_resp.status_code == 200, f"PUT /admin/capture failed: {put_resp.text}"

    app.state.completion_upstream = FakeCompletionUpstream(body=UPSTREAM_BODY_WITH_PII)  # type: ignore[attr-defined]

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, "please email me at user@example.com for details"),
        headers=auth_key(key_info["key"]),
    )
    assert resp.status_code == 200, f"completion failed: {resp.text}"

    await asyncio.sleep(0.3)

    rows = await _request_log_rows(db_session, tenant_id)
    assert len(rows) == 1, f"expected exactly 1 request_logs row, got {len(rows)}"
    req_body, resp_body, scrub_status, truncated, stream, cached = rows[0]
    dumped = json.dumps(req_body) + json.dumps(resp_body)
    print(f"\nscrub_status={scrub_status!r}\nreq_body={req_body}\nresp_body={resp_body}")

    # EXPECTED (per TASK.md §1 Must: "Raw, unscrubbed bodies never reach the INSERT
    # statement", GLOSSARY.md "Scrub-before-persist" invariant, and the milestone's own
    # feature name "PII-scrubbed ... payload capture store"): no raw PII substring should
    # ever be persisted, regardless of whether the tenant has separately configured
    # guardrails pii_mask — capture enabling ("PUT /admin/capture") is the only opt-in a
    # tenant should need for "PII-scrubbed" to actually mean something.
    #
    # ACTUAL: mask_pii_in_messages (guardrail_evaluator.py:229) is a pure pass-through
    # no-op unless the tenant has ALSO independently enabled guardrails pii_mask=mask
    # (guardrail_evaluator.py:253); TenantRow.guardrail_configs defaults to '{}' for every
    # tenant (tenants/infrastructure/orm.py:106). This tenant enabled ONLY capture (never
    # touched /admin/guardrails) — capture_writer.py still stamps scrub_status="scrubbed"
    # (the unconditional default, never revisited on this pass-through path), a false
    # label on a 100% raw row. REPRO CONFIRMED below (this assertion currently FAILS).
    assert "user@example.com" not in dumped, (
        "BLOCKER: raw, unmasked PII persisted to request_logs (scrub_status="
        f"{scrub_status!r}) for a tenant with capture enabled but guardrails pii_mask NOT "
        f"independently configured — scrub-before-persist is defeated by default for every "
        f"tenant that only ever calls PUT /admin/capture. dumped={dumped}"
    )
