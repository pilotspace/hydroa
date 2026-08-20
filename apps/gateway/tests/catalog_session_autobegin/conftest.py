"""Suite-local fixtures for catalog-sync-session-autobegin (.add/tasks/
catalog-sync-session-autobegin.md §CHECKS — direction beat, red-first).

Reuses the established sibling conventions rather than reinventing them:
  - the catalog fixtures (FakeCatalogSource / OPUS / SONNET / signup_and_login /
    active_model_count) are imported CROSS-SUITE from tests/catalog_sync_trigger/conftest.py
    — the motivating tree — so this suite and the tree it must return to green agree on
    what "the catalog was actually replaced" means. Cross-suite conftest import is the
    codebase idiom (tests/auth_hardening imports FakeEmailSender from
    tests/transactional_email/conftest.py the same way).
  - the impersonation seeding helpers mirror
    tests/impersonation_session_lifecycle/test_impersonation_session_lifecycle.py's own
    `_seed_impersonation_session` / `platform_tenant_id` (real FKs: actor_user_id,
    actor_tenant_id, target_user_id and target_tenant_id are ALL enforced, so every
    impersonated caller here seeds a REAL role='superadmin' users row).
  - the broken-store double mirrors tests/auth_hardening's
    `test_revocation_store_outage_fails_closed_bounded` monkeypatch of
    DbSessionRevocationGuard.is_revoked.

DO NOT weaken these fixtures (or the tests they serve) to make the suite pass — closing
the guards' read-only transaction is Build's job.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.tenants.domain.entities import ImpersonationContext, Role

# Cross-suite reuse — the motivating tree's own fixtures (see module docstring).
from tests.catalog_sync_trigger.conftest import (
    OPUS,
    SONNET,
    SYNC,
    FakeCatalogModel,
    FakeCatalogSource,
    active_model_count,
    auth,
    install_fake_source,
    signup_and_login,
)

ADMIN_MODELS = "/admin/models"
LOGOUT = "/admin/auth/logout"

# Re-exported so the tests import ONE surface: the motivating tree's fixtures and this
# suite's own helpers side by side.
__all__ = [
    "ADMIN_MODELS",
    "LOGOUT",
    "OPUS",
    "SONNET",
    "SYNC",
    "FakeCatalogModel",
    "FakeCatalogSource",
    "SessionTxnRecorder",
    "active_model_count",
    "active_model_ids",
    "auth",
    "install_fake_source",
    "install_txn_recorder",
    "mint_impersonated_owner_token",
    "owner_user_id",
    "platform_tenant_id",
    "seed_user",
    "signup_and_login",
]


async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id; seed one when the fast create_all test schema has
    not run the seed migration (mirrors every sibling platform-admin suite's fixture of
    the same name)."""
    from gateway.tenants.infrastructure.repository import get_platform_tenant

    tenant = await get_platform_tenant(db_session)
    if tenant is not None:
        return tenant.id

    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": tid},
    )
    await db_session.commit()
    return tid


async def seed_user(
    db_session: AsyncSession, *, tenant_id: uuid.UUID, email: str, role: str
) -> uuid.UUID:
    """Insert a users row directly (mirrors tests/admin_console_audit's own helper)."""
    uid = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, '$argon2id$v=dummy', :role)"
        ),
        {"id": uid, "tid": tenant_id, "email": email, "role": role},
    )
    await db_session.commit()
    return uid


async def mint_impersonated_owner_token(
    app: Any,
    db_session: AsyncSession,
    *,
    target_tenant_id: uuid.UUID,
    target_user_id: uuid.UUID,
    target_email: str,
) -> str:
    """Seed a LIVE impersonation_sessions row + mint the target-shaped JWT that carries it.

    A14: the catalog seam runs BOTH guards. The impersonation guard only queries when the
    identity actually carries an ImpersonationContext, so an impersonated caller is the
    case where BOTH guards read on the same request session — the ordering the fix has to
    survive. role stays OWNER (decode() rejects SUPERADMIN + impersonation together).
    """
    platform_id = await platform_tenant_id(db_session)
    actor_id = await seed_user(
        db_session,
        tenant_id=platform_id,
        email=f"root-{uuid.uuid4().hex[:8]}@platform.internal",
        role="superadmin",
    )
    actor_email = (
        await db_session.execute(text("SELECT email FROM users WHERE id = :i"), {"i": actor_id})
    ).scalar_one()

    session_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO impersonation_sessions
                (id, actor_user_id, actor_tenant_id, actor_email, target_user_id,
                 target_tenant_id, target_role, target_email, expires_at)
            VALUES
                (:id, :actor_user_id, :actor_tenant_id, :actor_email, :target_user_id,
                 :target_tenant_id, :target_role, :target_email, :expires_at)
            """
        ),
        {
            "id": session_id,
            "actor_user_id": actor_id,
            "actor_tenant_id": platform_id,
            "actor_email": actor_email,
            "target_user_id": target_user_id,
            "target_tenant_id": target_tenant_id,
            "target_role": "owner",
            "target_email": target_email,
            "expires_at": datetime.now(UTC) + timedelta(minutes=15),
        },
    )
    await db_session.commit()

    token, _ = app.state.token_service.issue(
        user_id=target_user_id,
        tenant_id=target_tenant_id,
        role=Role.OWNER,
        email=target_email,
        impersonation=ImpersonationContext(
            session_id=session_id,
            actor_user_id=actor_id,
            actor_tenant_id=platform_id,
            actor_email=actor_email,
        ),
        ttl_seconds=900,
    )
    return str(token)


async def owner_user_id(db_session: AsyncSession, *, email: str) -> uuid.UUID:
    row = (
        await db_session.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": email.lower()}
        )
    ).scalar_one()
    return uuid.UUID(str(row))


async def active_model_ids(db_session: AsyncSession) -> set[str]:
    """The ids the catalog actually holds — proves a 200 was not a 200-over-a-no-op (E1)."""
    rows = (await db_session.execute(text("SELECT id FROM models WHERE active = true"))).scalars()
    return {str(r) for r in rows}


class SessionTxnRecorder:
    """Records the transaction-ending calls made on ONE request-scoped AsyncSession, in
    order, interleaved with a marker for the moment the route handler's body starts.

    R:BLIND_COMMIT is invisible in the DB — the guards' transaction is read-only, so a
    commit() and a rollback() leave byte-identical state behind. The ONLY way to tell the
    contracted close from the rejected one is to watch which call the seam made, and when.
    Both spies delegate to the real method; nothing is stubbed out.
    """

    ROUTE_BODY = "route-body"

    def __init__(self) -> None:
        # (id of the session the call was made on, call name) — filtered at read time so a
        # commit on ANOTHER session (audit writer, background flusher) can never be
        # mistaken for the auth seam committing the guards' transaction.
        self._raw: list[tuple[int, str]] = []
        self.session_id: int | None = None
        self.in_transaction_at_route_body: bool | None = None

    def bind(self, session: Any) -> None:
        """Called from the route's own session dependency — i.e. AFTER the identity
        dependency (and both its guards) has already resolved on this same session."""
        self.session_id = id(session)
        self.in_transaction_at_route_body = bool(session.in_transaction())
        self._raw.append((self.session_id, self.ROUTE_BODY))

    def record(self, session: Any, name: str) -> None:
        self._raw.append((id(session), name))

    def before_route_body(self) -> list[str]:
        """Calls made on the REQUEST session before the route body started, in order."""
        mine = [(sid, name) for sid, name in self._raw if sid == self.session_id]
        names = [name for _sid, name in mine]
        if self.ROUTE_BODY not in names:
            return names
        return names[: names.index(self.ROUTE_BODY)]


def install_txn_recorder(app: Any, monkeypatch: Any) -> SessionTxnRecorder:
    """Wire a SessionTxnRecorder over `app`'s request session without stubbing anything.

    Two seams, both delegating:
      - `get_admin_models_session` is dependency-overridden to bind the recorder. It is
        declared AFTER the identity dependency on GET /admin/models, so FastAPI resolves
        it once the guards have run — and `get_session` is dependency-cached, so it is
        the SAME session object the guards read on.
      - AsyncSession.commit/rollback are wrapped (not replaced) so the real behaviour is
        unchanged and only the call is observed.
    """
    from gateway.catalog.api.deps import get_admin_models_session

    recorder = SessionTxnRecorder()

    real_commit = AsyncSession.commit
    real_rollback = AsyncSession.rollback

    async def _commit(self: Any) -> None:
        recorder.record(self, "commit")
        await real_commit(self)

    async def _rollback(self: Any) -> None:
        recorder.record(self, "rollback")
        await real_rollback(self)

    monkeypatch.setattr(AsyncSession, "commit", _commit)
    monkeypatch.setattr(AsyncSession, "rollback", _rollback)

    def _recording_session(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> AsyncSession:
        recorder.bind(session)
        return session

    app.dependency_overrides[get_admin_models_session] = _recording_session
    return recorder
