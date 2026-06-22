"""Suite-local fixtures for operator-wide-reconciliation (v31, TASK.md §4).

GET /ops/reconciliation is a CROSS-TENANT (all-tenants) read over the v29/v30
`reconcile_window(tenant_id=None)` aggregate + a new per-tenant breakdown, behind a
SEPARATE ops-auth surface: mTLS, terminated by Envoy, the validated operator cert
forwarded to the app as the `x-forwarded-client-cert` (XFCC) header. The app matches
the cert SHA-256 fingerprint against a configured allow-list (default-OFF / fail-closed).

The harness reuses the real Postgres (localhost:5433/gateway_test) + Redis + the root
`client`/`db_session`/`app` fixtures. Rows are seeded directly into the append-only
`usage_records` ledger with a controlled created_at; the endpoint is exercised over HTTP.
ASGITransport does NOT run lifespan, but this route needs no background task — only the
synchronously-registered router, the request-time ops dependency, and `app.state.settings`.
Ops-auth is enabled per-test by mutating `app.state.settings.ops_cert_fingerprints`
(function-scoped app => isolated).
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
OPS_RECON = "/ops/reconciliation"
PASSWORD = "correct horse battery"

# A configured operator cert fingerprint (SHA-256 hex, 64 chars) the allow-list trusts.
OPS_FP = "a" * 64
# A different, well-formed-but-unrecognised fingerprint.
OTHER_FP = "b" * 64

# Fixed UTC day the tests seed into; called with start=end=INSIDE_DATE (end-inclusive → +1 day),
# so the half-open window is [INSIDE_DATE 00:00, next-day 00:00) — deterministic.
INSIDE = datetime.datetime(2026, 6, 3, 12, 0, 0, tzinfo=datetime.UTC)
INSIDE_DATE = "2026-06-03"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def xfcc(fingerprint: str) -> dict[str, str]:
    """Build an Envoy-style XFCC header carrying an operator cert fingerprint (Hash field)."""
    return {"x-forwarded-client-cert": f'Hash={fingerprint};Subject="CN=ops-operator"'}


def enable_ops(app: Any, *fingerprints: str) -> None:
    """Turn the ops-auth surface ON for this test by setting the cert allow-list (CSV)."""
    app.state.settings.ops_cert_fingerprints = ",".join(fingerprints)


def recon_params(
    *, window: str = "month", start: str | None = INSIDE_DATE, end: str | None = INSIDE_DATE
) -> dict[str, str]:
    params: dict[str, str] = {"window": window}
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end
    return params


async def signup_tenant(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> tuple[str, str, str]:
    """Sign up a tenant+owner and create one key; return (owner_jwt, tenant_id, key_id)."""
    sr = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": PASSWORD}
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    token = lr.json()["access_token"]
    kr = await client.post(ADMIN_KEYS, json={"name": f"{tenant_name}-key"}, headers=auth(token))
    assert kr.status_code == 201, f"key creation failed: {kr.text}"
    return token, tenant_id, kr.json()["key_id"]


async def seed_row(
    session: AsyncSession,
    *,
    tenant_id: str,
    key_id: str,
    cost_usd: Decimal,
    created_at: datetime.datetime = INSIDE,
    provider_cost: Decimal | None = None,
    cost_basis: str = "provider",
    usage_source: str = "frame",
) -> None:
    """Insert one usage_records row with full reconciliation columns (controlled created_at)."""
    await session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, created_at, cost_basis, provider_cost, usage_source)"
            " VALUES (:id, :tid, :kid, :mid, 0, 0,"
            "  :cost, 200, '{}', :ts, :basis, :pcost, :src)"
        ),
        {
            "id": str(uuid.uuid4()),
            "tid": tenant_id,
            "kid": key_id,
            "mid": "openai/gpt-4o",
            "cost": str(cost_usd),
            # usage_records.created_at is naive TIMESTAMP in the test schema; strip tz.
            "ts": created_at.astimezone(datetime.UTC).replace(tzinfo=None),
            "basis": cost_basis,
            "pcost": str(provider_cost) if provider_cost is not None else None,
            "src": usage_source,
        },
    )


async def ledger_fingerprint(session: AsyncSession) -> tuple[int, Decimal, Decimal]:
    """(row count, Σ cost_usd, Σ provider_cost) — used to prove the read writes nothing."""
    row = (
        await session.execute(
            text(
                "SELECT COUNT(*),"
                " COALESCE(SUM(cost_usd),0),"
                " COALESCE(SUM(provider_cost),0)"
                " FROM usage_records"
            )
        )
    ).fetchone()
    assert row is not None
    return int(row[0]), Decimal(str(row[1])), Decimal(str(row[2]))
