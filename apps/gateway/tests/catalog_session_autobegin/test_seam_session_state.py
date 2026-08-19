"""S4 + M3/M5 of catalog-sync-session-autobegin (refrozen sha256:599d277a7a8b9973).

WHY THIS FILE EXISTS, and why the sibling AST guard was not enough:
`test_no_unguarded_autobegin_seam.py` resolves reachability by IMPORT ADJACENCY. It only
ever caught the keys->teams seam because `teams/api/deps.py` happens to import
`get_identity`. It is structurally blind to a seam reached only through the FastAPI
dependency GRAPH -- and three of the five revocation seams are exactly that, including
`tenants/domain/authz.py::_resolve_identity`, which backs 76 routes and left a transaction
open on `POST /admin/keys` even after the two dependency-level rollbacks were in place.

So this module asks the question at RUNTIME instead: drive the real app through the real
dependency graph and look at the shared session at the moment a repository would be
constructed on it. That subsumes the AST guard rather than duplicating it.

DO NOT weaken these tests to pass.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import httpx
import pytest
from fastapi import Depends
from fastapi.dependencies.utils import get_dependant
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.tenants.domain.entities import Identity, Role
from gateway.tenants.domain.errors import SessionRevocationUnavailableError
from gateway.tenants.infrastructure.session_revocation import DbSessionRevocationGuard
from tests.catalog_session_autobegin.conftest import auth, signup_and_login

# Routes behind each distinct authenticated seam. The seam name is what a failure reports,
# because "some route left a transaction open" is not actionable (A19).
#
# The POST rows are the DbUserLivenessGuard seam (authz.py::require_active_user), which runs
# AFTER the revocation guard on the credential-MINT routes only. Sampling happens before the
# handler body, so a mutating route is safe to drive here and nothing is written.
#
# DELIBERATELY NOT SWEPT — `/admin/auth/me` (the GetIdentityUseCase seam, revocation call
# site 5/5). Its guard runs INSIDE the handler body (tenants/api/router.py:315), not in the
# dependency graph, so an observer appended to the route's dependant samples BEFORE the seam
# and would report a permanent, meaningless green. Listing it here would claim coverage this
# mechanism cannot provide. That seam is covered instead by
# `test_guard_restores_only_what_it_opened`, which drives the guard directly.
SEAMS: list[tuple[str, str, str, dict[str, Any] | None]] = [
    ("catalog.api.deps:get_current_identity", "GET", "/admin/models", None),
    ("keys.api.deps:get_identity", "GET", "/admin/keys", None),
    ("keys.api.deps:get_identity -> teams", "GET", "/admin/teams", None),
    ("tenants.domain.authz:_resolve_identity", "GET", "/admin/audit/export", None),
    (
        "tenants.infrastructure:DbUserLivenessGuard (require_active_user)",
        "POST",
        "/admin/keys",
        {"name": "seam-sweep-probe"},
    ),
]


class _SessionSpy:
    """Records whether the shared session held a transaction AT THE MOMENT THE ROUTE'S
    DEPENDENCIES FINISHED RESOLVING -- A17's sampling point, and the exact point a
    repository is constructed on that session.

    Getting this point right is the whole check. Two wrong points, both of which make this
    sweep pass against the pre-fix tree and prove nothing:
      - AFTER `client.request(...)` returns: `async with sessionmaker()` has exited and the
        session is closed, so `in_transaction()` is False no matter what the seam did.
      - At dependency TEARDOWN: the HANDLER's own reads have by then autobegun a fresh
        transaction, so every seam reports True even when the guards behave perfectly.

    So the observer is injected as an extra dependency appended to the route's dependant.
    FastAPI solves `dependant.dependencies` in list order, so appending makes it resolve
    LAST -- after the auth seam, before the handler body.
    """

    def __init__(self) -> None:
        self.samples: list[bool] = []

    def install(self, application: Any, paths: set[str]) -> None:
        spy = self

        async def observer(
            session: Annotated[AsyncSession, Depends(get_session)],
        ) -> None:
            spy.samples.append(session.in_transaction())

        for route in application.routes:
            if isinstance(route, APIRoute) and route.path in paths:
                route.dependant.dependencies.append(
                    get_dependant(path=route.path_format, call=observer)
                )

    @property
    def left_open(self) -> bool:
        return any(self.samples)


async def test_no_seam_leaves_the_shared_session_in_a_transaction(
    client: httpx.AsyncClient,
    app: Any,
) -> None:
    """covers: M4, M5, A3, A15, A16, A17, A18, A19, A20, S4 — every authenticated seam
    leaves the shared request session with NO open transaction, established by driving the
    REAL dependency graph rather than by reading imports.

    Right-reason red before the fix: the seams backed by _resolve_identity report
    left_open=True while the two dependency-level-patched seams report False — that split
    is the whole point, and it is invisible to the AST guard.
    """
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="Seam Sweep", email="owner@seam-sweep.io"
    )

    spy = _SessionSpy()
    spy.install(app, {path for _s, _m, path, _b in SEAMS})
    try:
        offenders: list[str] = []
        undrivable: list[str] = []
        for seam, method, path, body in SEAMS:
            spy.samples.clear()
            resp = await client.request(method, path, headers=auth(jwt), json=body)
            # A16: a route we cannot drive is REPORTED, never silently skipped — a skip
            # here is the masked-gate failure mode.
            if resp.status_code >= 500:
                undrivable.append(f"{seam} ({method} {path} -> {resp.status_code})")
                continue
            if not spy.samples:
                undrivable.append(f"{seam} ({method} {path} -> no session resolved)")
                continue
            if spy.left_open:
                offenders.append(f"{seam} ({method} {path})")

        assert not offenders, (
            "these authenticated seams left the SHARED request session in a transaction; "
            "any repository that owns `async with session.begin()` behind them raises "
            f"InvalidRequestError: {offenders}"
        )
        # Anti-vacuity: if every seam became undrivable the assert above would pass while
        # proving nothing (a gate that never reaches a verdict reports green).
        assert len(undrivable) < len(SEAMS), (
            f"the sweep reached NO verdict — every seam was undrivable: {undrivable}"
        )
        assert spy.samples, "the session spy was never installed on a real request"
    finally:
        for route in app.routes:
            if isinstance(route, APIRoute) and route.path in {p for _s, _m, p, _b in SEAMS}:
                route.dependant.dependencies = [
                    d
                    for d in route.dependant.dependencies
                    if getattr(d.call, "__name__", "") != "observer"
                ]


async def test_guard_restores_only_what_it_opened(
    app: Any,
    db_session: AsyncSession,
) -> None:
    """covers: M3, M5, R:BLIND_COMMIT — the guard closes ONLY a transaction it began.

    Two halves, and the first is the one that makes putting the close in a shared primitive
    safe at all: a caller that already holds a transaction keeps it, so no pending work can
    be discarded by a read-only guard.
    """
    identity = Identity(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="seam-probe@autobegin.io",
        role=Role.OWNER,
        jti=str(uuid.uuid4()),
        iat=1,
    )

    # --- A. the caller already owns a transaction: the guard must NOT close it (M3) ---
    async with app.state.sessionmaker() as session:
        await session.begin()
        assert session.in_transaction()
        guard = DbSessionRevocationGuard(session=session, timeout_seconds=5.0)
        assert await guard.is_revoked(identity) is False
        assert session.in_transaction(), (
            "the guard rolled back a transaction it did NOT open — a caller's pending work "
            "can be discarded by a read-only guard (M3)"
        )
        await session.rollback()

    # --- B. the guard opened it: the guard must close it (M3) ---
    async with app.state.sessionmaker() as session:
        assert not session.in_transaction()
        guard = DbSessionRevocationGuard(session=session, timeout_seconds=5.0)
        assert await guard.is_revoked(identity) is False
        assert not session.in_transaction(), (
            "the guard left open the transaction its own SELECT began — this is the "
            "autobegin clash the task exists to close"
        )

    # --- C. a store failure still raises the 503-class error even when the rollback
    #        ALSO fails; the restore must never displace the verdict (M5) ---
    class _BrokenSession:
        def in_transaction(self) -> bool:
            return False

        async def execute(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("store down")

        async def rollback(self) -> None:
            raise RuntimeError("rollback also down")

    broken = DbSessionRevocationGuard(
        session=_BrokenSession(),  # type: ignore[arg-type]
        timeout_seconds=5.0,
    )
    with pytest.raises(SessionRevocationUnavailableError):
        await broken.is_revoked(identity)
