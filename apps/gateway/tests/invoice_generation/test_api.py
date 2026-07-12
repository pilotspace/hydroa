"""RED suite — GET /admin/invoices API surface (invoice-generation TASK.md §3 —
FROZEN @ v1).

Covers §2 scenarios that exercise the HTTP surface: immutability (M5), corrections
(M6), evidence drill-down (M7), tenant-scoped listing (M8), PDF/CSV/API total
agreement (M9), RBAC (M11), and every Reject-listed error (R1, R3-R8).

RED before BUILD: the routes/permission/error-codes do not exist yet, so every
200-expecting scenario 404s/403s-wrong-shape/500s — the honest missing-implementation
red. DO NOT weaken these tests to make them pass; that is Build's job.
"""

from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    JULY_START,
    LIST_INVOICES,
    assert_problem,
    auth,
    detail_url,
    evidence_url,
    export_url,
    make_generator,
    mint_role_token,
    seed_usage_record,
    signup_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.


async def _generate(app: Any, tenant_id: str, period_start: Any = JULY_START) -> str:
    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tenant_id), period_start)
    assert invoice_id is not None
    return str(invoice_id)


# ---------------------------------------------------------------------------
# M5 — issued invoice cannot be mutated
# ---------------------------------------------------------------------------


async def test_issued_invoice_cannot_be_mutated(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Immutable Co", email="immut@inv.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="5.00", created_at=JULY_START)
    invoice_id = await _generate(app, tid)

    before = (
        await db_session.execute(
            text("SELECT total_usd FROM invoices WHERE id = :id"), {"id": invoice_id}
        )
    ).scalar()

    with pytest.raises(Exception, match="invoice_immutable"):
        await db_session.execute(
            text("UPDATE invoices SET total_usd = 999.99 WHERE id = :id"), {"id": invoice_id}
        )
        await db_session.commit()
    await db_session.rollback()

    with pytest.raises(Exception, match="invoice_immutable"):
        await db_session.execute(
            text("DELETE FROM invoices WHERE id = :id"), {"id": invoice_id}
        )
        await db_session.commit()
    await db_session.rollback()

    after = (
        await db_session.execute(
            text("SELECT total_usd FROM invoices WHERE id = :id"), {"id": invoice_id}
        )
    ).scalar()
    assert before == after, "a re-read must be byte-identical to before the mutation attempt"


async def test_issued_invoice_line_cannot_be_mutated(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Immutable Line Co", email="il@inv.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="5.00", created_at=JULY_START)
    invoice_id = await _generate(app, tid)
    line_id = (
        await db_session.execute(
            text("SELECT id FROM invoice_lines WHERE invoice_id = :id"), {"id": invoice_id}
        )
    ).scalar()

    with pytest.raises(Exception, match="invoice_immutable"):
        await db_session.execute(
            text("UPDATE invoice_lines SET amount_usd = 0 WHERE id = :id"), {"id": str(line_id)}
        )
        await db_session.commit()
    await db_session.rollback()


# ---------------------------------------------------------------------------
# M6 — a correction is a new document, not an edit
# ---------------------------------------------------------------------------


async def test_correction_is_a_new_document_not_an_edit(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    from gateway.billing.application.invoice_correction import record_invoice_correction

    _owner, tid = await signup_tenant(client, tenant_name="Correction Co", email="corr@inv.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="100.00", created_at=JULY_START)
    invoice_id = await _generate(app, tid)
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="corr-sub@inv.io")

    await record_invoice_correction(
        app.state.sessionmaker,
        invoice_id=uuid.UUID(invoice_id),
        delta_usd=Decimal("-15.00"),
        reason="duplicate key double-counted",
        created_by="ops@hydroa.io",
    )

    row = (
        await db_session.execute(
            text("SELECT total_usd FROM invoices WHERE id = :id"), {"id": invoice_id}
        )
    ).mappings().one()
    assert Decimal(str(row["total_usd"])) == Decimal("100.00"), "original invoice untouched"

    resp = await client.get(detail_url(invoice_id), headers=auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["total_usd"]) == Decimal("100.00")
    assert Decimal(body["corrected_total_usd"]) == Decimal("85.00")
    assert len(body["corrections"]) == 1
    assert body["corrections"][0]["reason"] == "duplicate key double-counted"


# ---------------------------------------------------------------------------
# M7 — evidence drill-down resolves a disputed line to real usage rows
# ---------------------------------------------------------------------------


async def test_evidence_drilldown_resolves_line_to_usage_rows(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Evidence Co", email="ev@inv.io")
    key_id = str(uuid.uuid4())
    ids = []
    for i in range(5):
        rid = await seed_usage_record(
            db_session,
            tenant_id=tid,
            key_id=key_id,
            model_id="gpt-4o",
            cost_usd="1.00",
            created_at=JULY_START.replace(hour=(i % 23)),
        )
        ids.append(str(rid))
    # A row from a DIFFERENT model must never appear in this line's evidence.
    await seed_usage_record(
        db_session, tenant_id=tid, key_id=key_id, model_id="other-model",
        cost_usd="9.00", created_at=JULY_START,
    )

    invoice_id = await _generate(app, tid)
    line_row = (
        await db_session.execute(
            text(
                "SELECT id FROM invoice_lines WHERE invoice_id = :id AND model_id = 'gpt-4o'"
            ),
            {"id": invoice_id},
        )
    ).mappings().one()
    token = mint_role_token(app, tenant_id=tid, role=Role.BILLING_ADMIN, email="ev-sub@inv.io")

    resp = await client.get(
        evidence_url(invoice_id, str(line_row["id"])), params={"limit": "50"}, headers=auth(token)
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    returned_ids = {item["usage_record_id"] for item in body["items"]}
    assert returned_ids == set(ids)
    assert all(item["model_id"] == "gpt-4o" for item in body["items"])


# ---------------------------------------------------------------------------
# M8 — list returns only the caller's tenant, newest-period-first
# ---------------------------------------------------------------------------


async def test_list_returns_only_callers_tenant_newest_period_first(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner_a, tid_a = await signup_tenant(client, tenant_name="List Tenant A", email="la@inv.io")
    _owner_b, tid_b = await signup_tenant(client, tenant_name="List Tenant B", email="lb@inv.io")

    generator = make_generator(app)
    may_id = await generator.generate_for_tenant(
        uuid.UUID(tid_a), JULY_START.replace(month=5)
    )
    june_id = await generator.generate_for_tenant(
        uuid.UUID(tid_a), JULY_START.replace(month=6)
    )
    july_id = await generator.generate_for_tenant(uuid.UUID(tid_a), JULY_START)
    await generator.generate_for_tenant(uuid.UUID(tid_b), JULY_START)
    await generator.generate_for_tenant(uuid.UUID(tid_b), JULY_START.replace(month=6))

    token_a = mint_role_token(app, tenant_id=tid_a, role=Role.OWNER, email="la-sub@inv.io")

    resp = await client.get(LIST_INVOICES, headers=auth(token_a))

    assert resp.status_code == 200, resp.text
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == [str(july_id), str(june_id), str(may_id)]


# ---------------------------------------------------------------------------
# M9 — PDF, CSV, and API total always agree
# ---------------------------------------------------------------------------


async def test_pdf_csv_api_total_always_agree(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Export Co", email="exp@inv.io")
    for model in ("model-a", "model-b", "model-c", "model-d"):
        await seed_usage_record(
            db_session, tenant_id=tid, model_id=model, cost_usd="10.5425", created_at=JULY_START
        )
    invoice_id = await _generate(app, tid)
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="exp-sub@inv.io")

    detail_resp = await client.get(detail_url(invoice_id), headers=auth(token))
    csv_resp = await client.get(export_url(invoice_id, "csv"), headers=auth(token))
    pdf_resp = await client.get(export_url(invoice_id, "pdf"), headers=auth(token))

    assert detail_resp.status_code == 200, detail_resp.text
    assert csv_resp.status_code == 200, csv_resp.text
    assert pdf_resp.status_code == 200, pdf_resp.text

    api_total = Decimal(detail_resp.json()["total_usd"])

    reader = csv.DictReader(io.StringIO(csv_resp.text))
    csv_total = sum((Decimal(row["amount_usd"]) for row in reader), Decimal("0"))
    assert csv_total == api_total

    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert pdf_resp.headers["content-type"] == "application/pdf"
    assert "attachment" in csv_resp.headers.get("content-disposition", "")
    assert "attachment" in pdf_resp.headers.get("content-disposition", "")

    total_str = f"{api_total:.2f}".encode()
    assert total_str in pdf_resp.content, "PDF body must contain the exact printed total"


# ---------------------------------------------------------------------------
# M11, R2 — billing_admin/owner/admin/superadmin can read; operator/viewer/member cannot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.BILLING_ADMIN, Role.SUPERADMIN])
async def test_invoices_read_roles_pass(
    client: Any, db_session: AsyncSession, app: Any, role: Role
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name=f"Pass {role}", email=f"pass-{role}@inv.io"
    )
    token = mint_role_token(app, tenant_id=tid, role=role, email=f"pass-sub-{role}@inv.io")

    resp = await client.get(LIST_INVOICES, headers=auth(token))

    assert resp.status_code == 200, f"role={role} expected 200, got: {resp.text}"


@pytest.mark.parametrize("role", [Role.OPERATOR, Role.VIEWER, Role.MEMBER])
async def test_invoices_read_roles_forbidden(
    client: Any, db_session: AsyncSession, app: Any, role: Role
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name=f"Deny {role}", email=f"deny-{role}@inv.io"
    )
    token = mint_role_token(app, tenant_id=tid, role=role, email=f"deny-sub-{role}@inv.io")

    resp = await client.get(LIST_INVOICES, headers=auth(token))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ---------------------------------------------------------------------------
# R1 — no bearer token
# ---------------------------------------------------------------------------


async def test_no_bearer_token(client: Any) -> None:
    resp = await client.get(LIST_INVOICES)

    assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")
    assert "items" not in resp.json()


# ---------------------------------------------------------------------------
# R3 — invalid limit is rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit", ["0", "101", "abc"])
async def test_invalid_limit_rejected(
    client: Any, db_session: AsyncSession, app: Any, limit: str
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name=f"Limit {limit}", email=f"lim-{limit}@inv.io"
    )
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email=f"lim-sub-{limit}@inv.io")

    resp = await client.get(LIST_INVOICES, params={"limit": limit}, headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    assert "items" not in resp.json()


# ---------------------------------------------------------------------------
# R4 — malformed cursor is rejected
# ---------------------------------------------------------------------------


async def test_malformed_cursor_rejected(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Bad Cursor Co", email="bc@inv.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bc-sub@inv.io")

    resp = await client.get(
        LIST_INVOICES, params={"cursor": "not-valid-base64-or-wrong-shape"}, headers=auth(token)
    )

    assert_problem(resp, 422, "ERR_CURSOR_INVALID")


# ---------------------------------------------------------------------------
# R5 — unknown invoice id is 404; cross-tenant is the SAME 404
# ---------------------------------------------------------------------------


async def test_unknown_invoice_id_is_404(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Unknown Id Co", email="uid@inv.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="uid-sub@inv.io")

    resp = await client.get(detail_url(str(uuid.uuid4())), headers=auth(token))

    assert_problem(resp, 404, "ERR_INVOICE_NOT_FOUND")


async def test_cross_tenant_invoice_id_is_same_404(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner_a, tid_a = await signup_tenant(client, tenant_name="Cross A Co", email="ca@inv.io")
    _owner_b, tid_b = await signup_tenant(client, tenant_name="Cross B Co", email="cb@inv.io")
    await seed_usage_record(db_session, tenant_id=tid_b, cost_usd="2.00", created_at=JULY_START)
    invoice_id_b = await _generate(app, tid_b)
    token_a = mint_role_token(app, tenant_id=tid_a, role=Role.OWNER, email="ca-sub@inv.io")

    resp_unknown = await client.get(detail_url(str(uuid.uuid4())), headers=auth(token_a))
    resp_cross = await client.get(detail_url(invoice_id_b), headers=auth(token_a))

    assert_problem(resp_unknown, 404, "ERR_INVOICE_NOT_FOUND")
    assert_problem(resp_cross, 404, "ERR_INVOICE_NOT_FOUND")
    assert resp_unknown.json() == resp_cross.json(), "unknown vs cross-tenant must be byte-identical"


# ---------------------------------------------------------------------------
# R6 — evidence request against a mismatched line is 404
# ---------------------------------------------------------------------------


async def test_evidence_mismatched_line_is_404(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Mismatch Co", email="mm@inv.io")
    await seed_usage_record(
        db_session, tenant_id=tid, model_id="model-x", cost_usd="1.00", created_at=JULY_START
    )
    await seed_usage_record(
        db_session,
        tenant_id=tid,
        model_id="model-y",
        cost_usd="1.00",
        created_at=JULY_START.replace(month=6),
    )
    generator = make_generator(app)
    invoice_x = await generator.generate_for_tenant(uuid.UUID(tid), JULY_START)
    invoice_y = await generator.generate_for_tenant(
        uuid.UUID(tid), JULY_START.replace(month=6)
    )
    line_y = (
        await db_session.execute(
            text("SELECT id FROM invoice_lines WHERE invoice_id = :id"), {"id": str(invoice_y)}
        )
    ).scalar()
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="mm-sub@inv.io")

    resp = await client.get(
        evidence_url(str(invoice_x), str(line_y)), headers=auth(token)
    )

    assert_problem(resp, 404, "ERR_INVOICE_NOT_FOUND")


# ---------------------------------------------------------------------------
# R7 — bad export format is rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["xlsx", None])
async def test_bad_export_format_rejected(
    client: Any, db_session: AsyncSession, app: Any, fmt: str | None
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name=f"Format {fmt}", email=f"fmt-{fmt}@inv.io"
    )
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="1.00", created_at=JULY_START)
    invoice_id = await _generate(app, tid)
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email=f"fmt-sub-{fmt}@inv.io")

    resp = await client.get(export_url(invoice_id, fmt), headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# R8 — bounded query timeout surfaces as a structured error
# ---------------------------------------------------------------------------


async def test_query_timeout_maps_to_504(
    client: Any, db_session: AsyncSession, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Slow Invoices Co", email="sl@inv.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="1.00", created_at=JULY_START)
    await _generate(app, tid)
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="sl-sub@inv.io")

    orig_execute = AsyncSession.execute

    async def _flaky_execute(self: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        compiled = str(statement).lstrip()
        if compiled.startswith("SELECT") and "invoices" in compiled:
            raise TimeoutError("simulated invoices-query DB fault (test-only fault injection)")
        return await orig_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", _flaky_execute)

    resp = await client.get(LIST_INVOICES, headers=auth(token))

    assert_problem(resp, 504, "ERR_INVOICE_QUERY_TIMEOUT")


# ---------------------------------------------------------------------------
# edge-boundary — empty result set is 200 with an empty list
# ---------------------------------------------------------------------------


async def test_empty_result_is_200_empty_list(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="No Invoices Co", email="ni@inv.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="ni-sub@inv.io")

    resp = await client.get(LIST_INVOICES, headers=auth(token))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": [], "next_cursor": None, "has_more": False}
