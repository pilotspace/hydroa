"""RED suite for catalog-sync-session-autobegin (.add/tasks/catalog-sync-session-autobegin.md
§CHECKS — direction beat, red-first).

WHY THIS IS RED TODAY. Commit `42224201` (auth-hardening P0-1) wired
`ensure_session_not_revoked` into `catalog/api/deps.py:79`. Unlike the sibling
impersonation guard — which only queries when the identity actually carries an
ImpersonationContext — the revocation guard SELECTs on EVERY authenticated request, and
SQLAlchemy's AsyncSession autobegin turns that read into an open implicit transaction on
the request-scoped session. `SqlAlchemyCatalogRepository.sync_catalog`
(`catalog/infrastructure/repository.py:67`) then opens its own atomic boundary with
`async with self._session.begin()` and SQLAlchemy raises

    sqlalchemy.exc.InvalidRequestError: A transaction is already begun on this Session.

so `POST /admin/catalog/sync` is a hard failure for every authenticated console user.
Bisect: `tests/catalog_sync_trigger` is 9/9 green at `cd17ccf3` and 5/9 red at `42224201`
(reproduced on this tree: 5 failed, 4 passed).

The fix and its precedent are already in this codebase — `tenants/api/router.py:98-104`
closes a read-only lookup's autobegun transaction with `await session.rollback()` under a
comment naming this exact trap, and `tenants/infrastructure/repository.py:111` does the
same. Build closes the catalog seam the same way; these tests do not care HOW, only that
the guards still gate and the transaction is closed rather than committed.

DO NOT weaken these tests to pass. In particular: removing or relaxing
`ensure_session_not_revoked` at this seam trades one gated P0 for another (R:GATE_REMOVED),
and closing the transaction with `commit()` instead of `rollback()` would let a later
failure's half-formed state survive on the auth path (R:BLIND_COMMIT). Both are refused
below.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    ADMIN_MODELS,
    LOGOUT,
    OPUS,
    SONNET,
    SYNC,
    FakeCatalogSource,
    active_model_count,
    active_model_ids,
    auth,
    install_fake_source,
    install_txn_recorder,
    mint_impersonated_owner_token,
    owner_user_id,
    signup_and_login,
)


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    assert resp.json().get("code") == code, resp.text


# ---------------------------------------------------------------------------
async def test_catalog_sync_succeeds_for_authenticated_console_user(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    """covers: M1, A1, A2, A4, A5, A7, A14, E1 — POST /admin/catalog/sync as an
    authenticated owner returns 200 with the catalog replaced; RED today with
    InvalidRequestError surfacing as a 500 (tests/catalog_sync_trigger's 5 failures are the
    same defect and must return to green in the same run).

    Two accepted identities cross the seam, because they exercise DIFFERENT guard
    combinations on the one request session (A14):
      1. a plain console owner — only the revocation guard reads;
      2. an impersonated owner — BOTH guards read, the impersonation guard first.
    A close positioned between the two guards would pass (1) and fail (2), so (2) is what
    pins "strictly after both".

    Note on the observed failure shape (A7): the InvalidRequestError escapes as an
    unhandled exception, so under httpx's ASGITransport this call RAISES rather than
    returning a 500 body. Production's ServerErrorMiddleware turns the same escape into a
    500. Either way it is the defect, and either way this assertion cannot reach 200.
    """
    owner_token, tenant_id = await signup_and_login(
        client, tenant_name="Autobegin A", email="owner@autobegin.io"
    )

    # 1. plain console owner ------------------------------------------------
    install_fake_source(app, FakeCatalogSource(models=[OPUS, SONNET]))
    resp = await client.post(SYNC, headers=auth(owner_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["synced"] == 2
    datetime.datetime.fromisoformat(body["synced_at"])
    # E1: a 200 over a no-op is not a pass — the rows must actually be there.
    assert await active_model_ids(db_session) == {OPUS.id, SONNET.id}

    # 2. impersonated console owner (A14: both guards read on the same session) ------
    target_id = await owner_user_id(db_session, email="owner@autobegin.io")
    impersonated = await mint_impersonated_owner_token(
        app,
        db_session,
        target_tenant_id=uuid.UUID(str(tenant_id)),
        target_user_id=target_id,
        target_email="owner@autobegin.io",
    )
    install_fake_source(app, FakeCatalogSource(models=[OPUS]))
    imp_resp = await client.post(SYNC, headers=auth(impersonated))

    assert imp_resp.status_code == 200, imp_resp.text
    assert imp_resp.json()["synced"] == 1
    # The impersonated sync really ran: SONNET is gone, OPUS remains (E1 again).
    assert await active_model_ids(db_session) == {OPUS.id}


# ---------------------------------------------------------------------------
async def test_revocation_still_enforced_on_catalog_seam(
    client: Any, app: Any, db_session: AsyncSession, monkeypatch: Any
) -> None:
    """covers: M2, M3, A6, E2, E3, R:GATE_REMOVED, R:BLIND_COMMIT — a denylisted jti is
    still refused 401 at /admin/catalog/sync, and a failing revocation store still yields
    503 ERR_AUTH_UNAVAILABLE — so the 500 cannot be "fixed" by dropping the gate; asserts
    the guards' transaction was closed rather than committed.

    Phases A and B are the REGRESSION floor and pass on today's tree by construction — they
    exist to stay green through Build (R:GATE_REMOVED). Phase C is what is RED today, and
    it is red for a DIFFERENT reason than the check above: no repository is reached at all
    on GET /admin/models, so there is no InvalidRequestError here. The failure is that the
    guards' implicit transaction is still OPEN when the route body starts, and that no
    rollback was ever issued on that session.
    """
    owner_token, _tid = await signup_and_login(
        client, tenant_name="Autobegin B", email="owner@autobegin-b.io"
    )

    # --- A. a denylisted jti is still refused at the catalog seam (M2, E2, A6) ------
    install_fake_source(app, FakeCatalogSource(models=[OPUS, SONNET]))
    assert (await client.post(LOGOUT, headers=auth(owner_token))).status_code == 204

    revoked_resp = await client.post(SYNC, headers=auth(owner_token))
    assert revoked_resp.status_code == 401, (
        f"gate removed: a revoked console token synced the catalog "
        f"({revoked_resp.status_code}) — R:GATE_REMOVED"
    )
    # A6: authz refuses BEFORE any transaction bookkeeping, so nothing was written.
    assert await active_model_count(db_session) == 0

    # --- B. a revocation-store failure is still a 503, never a 401/500/allow (M2, E3) ---
    live_token, _tid2 = await signup_and_login(
        client, tenant_name="Autobegin C", email="owner@autobegin-c.io"
    )
    from gateway.tenants.infrastructure import session_revocation

    async def _broken(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("revocation store down (test double)")

    monkeypatch.setattr(session_revocation.DbSessionRevocationGuard, "is_revoked", _broken)
    install_fake_source(app, FakeCatalogSource(models=[OPUS]))
    outage_resp = await client.post(SYNC, headers=auth(live_token))

    assert_problem(outage_resp, 503, "ERR_AUTH_UNAVAILABLE")
    assert await active_model_count(db_session) == 0
    monkeypatch.undo()

    # --- C. an ACCEPTED identity leaves the seam with a CLOSED, not committed, txn -----
    # GET /admin/models runs the same get_current_identity seam and then a read-only body,
    # so nothing here depends on the repository. What is observed is only the disposition
    # of the transaction the guards autobegan.
    recorder = install_txn_recorder(app, monkeypatch)
    models_resp = await client.get(ADMIN_MODELS, headers=auth(live_token))
    assert models_resp.status_code == 200, models_resp.text

    assert recorder.in_transaction_at_route_body is False, (
        "the guards' read-only transaction was still OPEN when the route body started — "
        "this is the autobegin that makes SqlAlchemyCatalogRepository.sync_catalog's "
        "`async with self._session.begin()` raise InvalidRequestError. Close it in the "
        "dependency that opened it (precedent: tenants/api/router.py:98-104)."
    )
    before = recorder.before_route_body()
    assert "commit" not in before, (
        f"R:BLIND_COMMIT — the auth seam COMMITTED the guards' transaction "
        f"({before}); a commit on the auth path can persist half-formed state a later "
        f"failure should have discarded. Roll back, do not commit."
    )
    assert "rollback" in before, (
        f"M3 — the guards' transaction was never closed by the seam that opened it "
        f"(calls seen before the route body: {before}). Expected exactly the shipped "
        f"precedent: `await session.rollback()` after BOTH guards have run."
    )
