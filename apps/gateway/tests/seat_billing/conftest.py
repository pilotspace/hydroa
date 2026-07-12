"""Suite-local fixtures/helpers for seat-billing (TASK.md §3 CONTRACT — FROZEN @ v2).

Reuses the top-level `app`/`client`/`db_session` fixtures (real Postgres) plus TWO
cross-suite imports (established precedent — tests/cache_alias_billing,
tests/per_key_guardrail_policies, tests/output_schema_validation all import a sibling
suite's conftest directly):
  - tests/invoice_generation/conftest.py: `signup_tenant`/`mint_role_token`/
    `make_generator`/`assert_problem` — this task's own pricing math is exercised via the
    SAME InvoiceGenerator wiring invoice-generation's own suite already established.
  - tests/plan_seat_cap/conftest.py: `signup_owner`/`create_scim_token`/`scim_bearer`/
    `build_oidc_app`/`oidc_callback`/OIDC fakes — the SAME 4 member-creating seams this
    task instruments were JUST wired for seat-CAP by that sibling task; reusing its real-
    flow harness is the only way to drive `_get_or_provision_sso_user`/
    `join_verified_tenant_domain`/SCIM create through the real HTTP surface rather than
    re-deriving OIDC id-token/SAML assertion plumbing from scratch.

The domain-capture DNS fake is DELIBERATELY duplicated here, NOT cross-imported —
mirrors tests/plan_seat_cap/test_domain_capture_seat_cap.py's own stated precedent
("not imported cross-suite to keep this suite self-contained"), a tiny (~20 line) local
double.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Re-exported for `from .conftest import X` convenience in every seat_billing test file.
from tests.invoice_generation.conftest import (  # noqa: F401
    assert_problem,
    auth,
    make_generator,
    mint_role_token,
    signup_tenant,
)
from tests.plan_seat_cap.conftest import (  # noqa: F401
    FAKE_OIDC_NONCE,
    FAKE_OIDC_STATE,
    FakeOidcExchanger,
    active_user_count,
    assign_plan,
    bearer,
    build_oidc_app,
    create_scim_token,
    issue_token,
    make_oidc_id_token,
    oidc_callback,
    scim_bearer,
    signup_owner,
    user_exists,
)

# A fixed base UTC instant + the July 2026 calendar-month period — mirrors
# tests/invoice_generation/conftest.py's own constants exactly (period-boundary math
# must agree byte-for-byte between the two suites' fixtures).
JULY_START = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
AUGUST_START = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)


def detail_url(invoice_id: str) -> str:
    return f"/admin/invoices/{invoice_id}"


def seat_evidence_url(invoice_id: str, line_id: str) -> str:
    return f"/admin/invoices/{invoice_id}/lines/{line_id}/seat-evidence"


def usage_evidence_url(invoice_id: str, line_id: str) -> str:
    return f"/admin/invoices/{invoice_id}/lines/{line_id}/evidence"


def _naive(dt: datetime.datetime) -> datetime.datetime:
    """asyncpg rejects an aware datetime into the create_all naive TIMESTAMP column
    (mirrors invoice_generation/conftest.py's own `_naive` helper)."""
    return dt.astimezone(datetime.UTC).replace(tzinfo=None) if dt.tzinfo else dt


async def seed_plan_with_seat_price(
    db_session: AsyncSession, *, name: str, seat_price: str | None
) -> str:
    """Insert a `plans` row directly via ORM with an explicit seat_price_usd_monthly —
    create_all doesn't replay the migration's own seed INSERT (mirrors
    tests/plan_seat_cap/conftest.py's own `seed_plan`)."""
    from gateway.tenants.infrastructure.orm import PlanRow

    row = PlanRow(
        id=uuid.uuid4(),
        name=name,
        display_name=name.title(),
        seat_cap=None,
        budget_usd_monthly_default=None,
        rpm_limit_default=None,
        tpm_limit_default=None,
        model_allowlist=None,
        feature_flags=[],
        seat_price_usd_monthly=Decimal(seat_price) if seat_price is not None else None,
    )
    db_session.add(row)
    await db_session.commit()
    return str(row.id)


async def seed_user(
    db_session: AsyncSession,
    *,
    tenant_id: str,
    email: str | None = None,
    role: str = "member",
    created_at: datetime.datetime = JULY_START,
    deactivated_at: datetime.datetime | None = None,
) -> str:
    """Seed ONE `users` row directly via SQL (bypassing the real provisioning seams —
    the legitimate arrange step for pricing-focused tests, mirrors
    tests/plan_seat_cap/conftest.py's own `seed_extra_active_users` idiom). Deliberately
    writes NO `seat_membership_events` row — callers that need one call `seed_event`
    separately (M5's fallback test relies on exactly this gap)."""
    user_id = uuid.uuid4()
    resolved_email = email or f"seed-{uuid.uuid4().hex[:8]}@seatbilling.test"
    await db_session.execute(
        text(
            "INSERT INTO users"
            " (id, tenant_id, email, password_hash, role, auth_method, created_at, deactivated_at)"
            " VALUES"
            " (:id, :tid, :email, 'x', :role, 'password', :created_at, :deactivated_at)"
        ),
        {
            "id": user_id,
            "tid": tenant_id,
            "email": resolved_email,
            "role": role,
            "created_at": _naive(created_at),
            "deactivated_at": deactivated_at,
        },
    )
    await db_session.commit()
    return str(user_id)


async def seed_event(
    db_session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    event_type: str,
    occurred_at: datetime.datetime,
) -> str:
    """Seed ONE `seat_membership_events` row directly via SQL — the legitimate arrange
    step for pricing-math tests that need a deterministic, hand-authored event stream
    (M6/M7 scenarios), independent of the real write-site tests (which exercise the
    actual seam instrumentation instead)."""
    event_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO seat_membership_events (id, tenant_id, user_id, event_type, occurred_at)"
            " VALUES (:id, :tid, :uid, :et, :oa)"
        ),
        {
            "id": event_id,
            "tid": tenant_id,
            "uid": user_id,
            "et": event_type,
            "oa": occurred_at,
        },
    )
    await db_session.commit()
    return str(event_id)


async def membership_events_for_user(
    db_session: AsyncSession, *, user_id: str
) -> list[dict[str, Any]]:
    rows = (
        (
            await db_session.execute(
                text(
                    "SELECT id, event_type, occurred_at FROM seat_membership_events"
                    " WHERE user_id = :uid ORDER BY occurred_at, id"
                ),
                {"uid": user_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def get_invoice_detail(
    client: httpx.AsyncClient, *, token: str, invoice_id: str
) -> dict[str, Any]:
    resp = await client.get(detail_url(invoice_id), headers=auth(token))
    assert resp.status_code == 200, f"invoice detail fetch failed: {resp.text}"
    return dict(resp.json())


def lines_of_type(detail: dict[str, Any], line_type: str) -> list[dict[str, Any]]:
    return [line for line in detail["lines"] if line["line_type"] == line_type]


# ---------------------------------------------------------------------------
# domain-capture — a self-contained local double (mirrors plan_seat_cap's own
# test_domain_capture_seat_cap.py precedent: "not imported cross-suite to keep this
# suite self-contained").
# ---------------------------------------------------------------------------


class FakeDnsResolverForSeatBilling:
    def __init__(self) -> None:
        self._records: dict[str, list[str]] = {}

    def set_record(self, name: str, token: str) -> None:
        self._records[name] = [f"ai-proxy-domain-verification={token}"]

    async def lookup_txt(self, name: str, *, timeout: float) -> list[str]:  # noqa: ASYNC109
        return list(self._records.get(name, []))


def domain_record_name(domain: str) -> str:
    return f"_ai-proxy-challenge.{domain}"


async def claim_and_verify_domain(
    client: httpx.AsyncClient,
    *,
    owner_token: str,
    domain: str,
    fake_dns: FakeDnsResolverForSeatBilling,
) -> None:
    create = await client.post(
        "/admin/domain-claims", json={"domain": domain}, headers=bearer(owner_token)
    )
    assert create.status_code == 201, f"setup failed creating domain claim: {create.text}"
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(domain_record_name(domain), claim_token)

    verify = await client.post(
        f"/admin/domain-claims/{claim_id}/verify", headers=bearer(owner_token)
    )
    assert verify.status_code == 200, f"setup failed verifying domain claim: {verify.text}"
