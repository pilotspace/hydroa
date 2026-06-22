"""RED suite for operator-wide-reconciliation (v31, TASK.md §4, scenarios OW1–OW8).

GET /ops/reconciliation is a CROSS-TENANT read over reconcile_window(tenant_id=None) +
a new per-tenant breakdown, behind a SEPARATE ops-auth surface (mTLS → Envoy → XFCC
header; app matches the cert SHA-256 fingerprint against a default-OFF allow-list).

RED before BUILD: the /ops route, the `ops_cert_fingerprints` setting, the new
`reconcile_by_tenant` aggregate, and the `OpsCertVerifier` do not exist yet — the honest
missing-implementation red. Unit-test imports are lazy so a missing symbol reds only its
own test, leaving the endpoint tests collectable. Money is str(Decimal); assertions compare
the Decimal VALUE (robust to Postgres NUMERIC scale).

SECURITY INVARIANTS UNDER TEST (the risk:high core):
  - a valid TENANT JWT can NEVER read cross-tenant data (403, distinct from 401);
  - all auth-failure modes are byte-identical 401 (no oracle);
  - default-OFF / fail-closed: no configured operator identity ⇒ nobody is authorized.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    OPS_FP,
    OPS_RECON,
    OTHER_FP,
    auth,
    enable_ops,
    ledger_fingerprint,
    recon_params,
    seed_row,
    signup_tenant,
    xfcc,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"


def assert_no_data(resp: Any) -> None:
    """A denied response must not leak any reconciliation payload."""
    body = resp.json()
    assert "drift" not in body and "by_tenant" not in body, f"leaked data: {body}"


# ---------------------------------------------------------------------------
# OW1 — an operator reads cross-tenant + per-tenant drift
# ---------------------------------------------------------------------------
async def test_ow1_operator_reads_cross_tenant(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    enable_ops(app, OPS_FP)
    _tok_a, tid_a, kid_a = await signup_tenant(client, tenant_name="OW1 A", email="ow1a@ops.io")
    _tok_b, tid_b, kid_b = await signup_tenant(client, tenant_name="OW1 B", email="ow1b@ops.io")
    # tenant A: provider_cost 1.00 billed 0 → unbilled, drift +1.00
    await seed_row(
        db_session,
        tenant_id=tid_a,
        key_id=kid_a,
        cost_usd=Decimal("0"),
        provider_cost=Decimal("1.00"),
        cost_basis="provider",
    )
    # tenant B: provider_cost 2.00 billed 0 → unbilled, drift +2.00
    await seed_row(
        db_session,
        tenant_id=tid_b,
        key_id=kid_b,
        cost_usd=Decimal("0"),
        provider_cost=Decimal("2.00"),
        cost_basis="provider",
    )
    await db_session.commit()

    resp = await client.get(OPS_RECON, params=recon_params(), headers=xfcc(OPS_FP))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # global aggregate spans BOTH tenants (the consciously-broken tenant-scope invariant)
    assert isinstance(body["drift"], str)  # money is str(Decimal), never float
    assert Decimal(body["provider_cost_total"]) == Decimal("3.00")
    assert Decimal(body["billed_total"]) == Decimal("0")
    assert Decimal(body["drift"]) == Decimal("3.00")
    assert Decimal(body["unbilled_upstream_cost"]) == Decimal("3.00")
    # per-tenant breakdown: one row per tenant, each carrying its own drift
    by_tenant = {row["tenant_id"]: row for row in body["by_tenant"]}
    assert set(by_tenant) == {tid_a, tid_b}
    assert Decimal(by_tenant[tid_a]["drift"]) == Decimal("1.00")
    assert Decimal(by_tenant[tid_b]["drift"]) == Decimal("2.00")
    # per-tenant unbilled fields are correct (both rows are billed $0 → fully unbilled)
    assert Decimal(by_tenant[tid_a]["unbilled_upstream_cost"]) == Decimal("1.00")
    assert by_tenant[tid_a]["unbilled_rows"] == 1
    assert Decimal(by_tenant[tid_b]["unbilled_upstream_cost"]) == Decimal("2.00")
    # the per-tenant drifts sum to the global drift (no double-count, no missed tenant)
    assert sum(Decimal(r["drift"]) for r in body["by_tenant"]) == Decimal(body["drift"])


# ---------------------------------------------------------------------------
# OW2 — the operator read writes nothing (read-only)
# ---------------------------------------------------------------------------
async def test_ow2_read_writes_nothing(client: Any, db_session: AsyncSession, app: Any) -> None:
    enable_ops(app, OPS_FP)
    _tok, tid, kid = await signup_tenant(client, tenant_name="OW2", email="ow2@ops.io")
    await seed_row(
        db_session,
        tenant_id=tid,
        key_id=kid,
        cost_usd=Decimal("1.20"),
        provider_cost=Decimal("1.00"),
        cost_basis="provider",
    )
    await db_session.commit()
    before = await ledger_fingerprint(db_session)

    resp = await client.get(OPS_RECON, params=recon_params(), headers=xfcc(OPS_FP))

    assert resp.status_code == 200, resp.text
    after = await ledger_fingerprint(db_session)
    assert after == before  # row count + Σ cost_usd + Σ provider_cost unchanged


# ---------------------------------------------------------------------------
# OW3 — a valid TENANT JWT is denied DISTINCTLY (403, never reaches cross-tenant data)
# ---------------------------------------------------------------------------
async def test_ow3_tenant_jwt_forbidden(client: Any, db_session: AsyncSession, app: Any) -> None:
    enable_ops(app, OPS_FP)
    owner_jwt, _tid, _kid = await signup_tenant(client, tenant_name="OW3", email="ow3@ops.io")

    # a valid tenant owner token, NO operator cert → explicit "wrong credential type"
    resp = await client.get(OPS_RECON, params=recon_params(), headers=auth(owner_jwt))

    assert_problem(resp, 403, "ERR_OPS_FORBIDDEN")
    assert_no_data(resp)


# ---------------------------------------------------------------------------
# OW4 — missing / unrecognised / malformed operator identity → byte-identical 401
# ---------------------------------------------------------------------------
async def test_ow4_unauthorized_byte_identical(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    enable_ops(app, OPS_FP)

    missing = await client.get(OPS_RECON, params=recon_params())  # no XFCC at all
    unknown = await client.get(OPS_RECON, params=recon_params(), headers=xfcc(OTHER_FP))
    malformed = await client.get(
        OPS_RECON,
        params=recon_params(),
        headers={"x-forwarded-client-cert": "this-is-not-a-valid-xfcc"},
    )
    # an INVALID Bearer (no XFCC) must NOT yield the 403 oracle — only a VALID tenant JWT does.
    # Guards the `except InvalidTokenError: pass` branch in require_ops against regression.
    bad_bearer = await client.get(
        OPS_RECON, params=recon_params(), headers={"Authorization": "Bearer not.a.real.jwt"}
    )

    for resp in (missing, unknown, malformed, bad_bearer):
        assert_problem(resp, 401, "ERR_OPS_UNAUTHORIZED")
        assert_no_data(resp)
    # no oracle: every failure mode is indistinguishable, byte for byte
    assert missing.text == unknown.text == malformed.text == bad_bearer.text


# ---------------------------------------------------------------------------
# OW5 — ops-auth NOT configured (default-OFF) → fail-closed 401 even for a well-formed XFCC
# ---------------------------------------------------------------------------
async def test_ow5_not_configured_fail_closed(client: Any, app: Any) -> None:
    # NOTE: enable_ops is deliberately NOT called → allow-list is empty (default-OFF).
    resp = await client.get(OPS_RECON, params=recon_params(), headers=xfcc(OPS_FP))
    no_cred = await client.get(OPS_RECON, params=recon_params())  # same not-configured app

    assert_problem(resp, 401, "ERR_OPS_UNAUTHORIZED")
    assert_no_data(resp)
    # not-configured is byte-identical to the generic unauthorized body (no oracle on config state)
    assert resp.text == no_cred.text


# ---------------------------------------------------------------------------
# OW6 — an invalid window value is rejected (reuse existing param validation)
# ---------------------------------------------------------------------------
async def test_ow6_bad_window_422(client: Any, app: Any) -> None:
    enable_ops(app, OPS_FP)

    resp = await client.get(OPS_RECON, params={"window": "century"}, headers=xfcc(OPS_FP))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# OW7 — unit: reconcile_by_tenant groups drift per tenant (GROUP BY tenant_id)
# ---------------------------------------------------------------------------
async def test_ow7_reconcile_by_tenant_groups(client: Any, db_session: AsyncSession) -> None:
    from datetime import UTC, datetime

    from gateway.usage.application.reconciliation import reconcile_by_tenant

    _tok_a, tid_a, kid_a = await signup_tenant(client, tenant_name="OW7 A", email="ow7a@ops.io")
    _tok_b, tid_b, kid_b = await signup_tenant(client, tenant_name="OW7 B", email="ow7b@ops.io")
    await seed_row(
        db_session,
        tenant_id=tid_a,
        key_id=kid_a,
        cost_usd=Decimal("0.50"),
        provider_cost=Decimal("1.00"),
        cost_basis="provider",
    )
    await seed_row(
        db_session,
        tenant_id=tid_b,
        key_id=kid_b,
        cost_usd=Decimal("0"),
        provider_cost=Decimal("2.00"),
        cost_basis="provider",
    )
    await db_session.commit()

    window_from = datetime(2026, 6, 3, tzinfo=UTC)
    window_to = datetime(2026, 6, 4, tzinfo=UTC)
    rows = await reconcile_by_tenant(db_session, window_from, window_to)

    by_tenant = {str(r.tenant_id): r for r in rows}
    assert set(by_tenant) == {tid_a, tid_b}
    assert by_tenant[tid_a].drift == Decimal("0.50")  # 1.00 - 0.50
    assert by_tenant[tid_b].drift == Decimal("2.00")  # 2.00 - 0
    assert by_tenant[tid_b].unbilled_rows == 1


# ---------------------------------------------------------------------------
# OW8 — unit: OpsCertVerifier matches a configured fingerprint, fails closed otherwise
# ---------------------------------------------------------------------------
def test_ow8_verifier_matches_fingerprint() -> None:
    from gateway.tenants.infrastructure.ops_cert_verifier import OpsCertVerifier

    verifier = OpsCertVerifier(frozenset({OPS_FP}))
    # a recognised fingerprint in a well-formed XFCC → an OpsIdentity (truthy)
    identity = verifier.identify(f'Hash={OPS_FP};Subject="CN=ops-operator"')
    assert identity is not None
    assert identity.fingerprint == OPS_FP

    # every fail-closed path → None
    assert verifier.identify(None) is None
    assert verifier.identify(f"Hash={OTHER_FP}") is None  # unrecognised
    assert verifier.identify("garbage-not-xfcc") is None  # malformed
    assert verifier.identify("") is None

    # empty allow-list (default-OFF) → nobody is authorized, even a well-formed XFCC
    off = OpsCertVerifier(frozenset())
    assert off.identify(f"Hash={OPS_FP}") is None


# ---------------------------------------------------------------------------
# OW9 — catalog rows are reported separately, never folded into drift (global or per-tenant)
# ---------------------------------------------------------------------------
async def test_ow9_catalog_not_in_drift(client: Any, db_session: AsyncSession, app: Any) -> None:
    enable_ops(app, OPS_FP)
    _tok, tid, kid = await signup_tenant(client, tenant_name="OW9", email="ow9@ops.io")
    # provider row: pcost 1.00 billed 1.20 → drift -0.20
    await seed_row(
        db_session,
        tenant_id=tid,
        key_id=kid,
        cost_usd=Decimal("1.20"),
        provider_cost=Decimal("1.00"),
        cost_basis="provider",
    )
    # catalog rows: billed 4.00, provider_cost NULL — must NOT touch drift
    await seed_row(
        db_session,
        tenant_id=tid,
        key_id=kid,
        cost_usd=Decimal("2.50"),
        provider_cost=None,
        cost_basis="catalog",
    )
    await seed_row(
        db_session,
        tenant_id=tid,
        key_id=kid,
        cost_usd=Decimal("1.50"),
        provider_cost=None,
        cost_basis="catalog",
    )
    await db_session.commit()

    resp = await client.get(OPS_RECON, params=recon_params(), headers=xfcc(OPS_FP))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["drift"]) == Decimal("-0.20")  # provider-only, catalog excluded
    assert Decimal(body["catalog_billed_total"]) == Decimal("4.00")  # reported separately
    # per-tenant drift is also provider-only (catalog never folded in)
    by_tenant = {row["tenant_id"]: row for row in body["by_tenant"]}
    assert Decimal(by_tenant[tid]["drift"]) == Decimal("-0.20")
