"""RED suite for agent-oauth-grant-store (§2 SCENARIOS of TASK.md).

One test per scenario. Asserts OBSERVABLE behavior: persisted rows, hashing-at-rest,
state transitions, atomic single-use mint, fail-closed token resolution, tenant isolation,
and every named rejection code.

All tests fail at COLLECTION with ImportError ("gateway.agent_oauth.* does not exist yet")
until the Build phase — the right red reason.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.agent_oauth.application.use_cases import AgentOAuthService
from gateway.agent_oauth.domain.errors import (
    AuthorizationAlreadyConsumedError,
    AuthorizationExpiredError,
    AuthorizationNotApprovedError,
    AuthorizationNotPendingError,
    DeviceCodeCollisionError,
)
from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)


async def _start(service: AgentOAuthService, *, now: datetime = _NOW, ttl: int = 600):
    return await service.start_device_authorization(
        scope="proxy", interval_seconds=5, ttl_seconds=ttl, now=now
    )


# ── Must: create a pending authorization ─────────────────────────────────────


async def test_create_pending_persists_unbound_pending_row(
    service: AgentOAuthService, db_session: AsyncSession, tenant_user: dict[str, uuid.UUID]
) -> None:
    minted = await _start(service)
    auth = minted.authorization
    assert auth.status == "pending"
    assert auth.tenant_id is None and auth.user_id is None
    assert auth.interval_seconds == 5
    assert auth.expires_at > _NOW
    row = await db_session.execute(
        text("SELECT status, tenant_id, user_id FROM device_authorizations WHERE id = :i"),
        {"i": auth.id},
    )
    status, tid, uid = row.one()
    assert status == "pending"
    assert tid is None and uid is None


# ── Must: secrets hashed at rest, plaintext returned once ────────────────────


async def test_secrets_are_hashed_at_rest_never_plaintext(
    service: AgentOAuthService,
    db_session: AsyncSession,
    hasher: Sha256SecretHasher,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    minted = await _start(service)
    auth_id = minted.authorization.id
    row = await db_session.execute(
        text("SELECT device_code_hash, user_code_hash FROM device_authorizations WHERE id = :i"),
        {"i": auth_id},
    )
    dch, uch = row.one()
    # stored value is the SHA-256 hash, NOT the plaintext
    assert dch == hasher.hash(minted.device_code)
    assert uch == hasher.hash(minted.user_code)
    assert minted.device_code not in (dch, uch)
    assert minted.user_code not in (dch, uch)


async def test_token_secrets_are_hashed_at_rest(
    service: AgentOAuthService,
    repo: SqlAlchemyAgentOAuthRepository,
    db_session: AsyncSession,
    hasher: Sha256SecretHasher,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    """Access AND refresh token plaintext are never persisted — only their SHA-256 hashes."""
    minted = await _start(service)
    await repo.approve(
        authorization_id=minted.authorization.id,
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
        now=_NOW,
    )
    token = await service.mint(
        authorization_id=minted.authorization.id,
        access_ttl_seconds=3600,
        refresh_ttl_seconds=2592000,
        now=_NOW,
    )
    assert token.refresh_token is not None
    row = await db_session.execute(
        text("SELECT access_token_hash, refresh_token_hash FROM agent_tokens WHERE id = :i"),
        {"i": token.token.id},
    )
    ath, rth = row.one()
    assert ath == hasher.hash(token.access_token)
    assert rth == hasher.hash(token.refresh_token)
    # neither plaintext is present in either stored column
    assert token.access_token not in (ath, rth)
    assert token.refresh_token not in (ath, rth)


# ── Must: lookup by device_code hash AND by user_code hash ───────────────────


async def test_lookup_by_device_and_user_code_hash(
    service: AgentOAuthService,
    repo: SqlAlchemyAgentOAuthRepository,
    hasher: Sha256SecretHasher,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    minted = await _start(service)
    by_device = await repo.get_by_device_code_hash(hasher.hash(minted.device_code))
    by_user = await repo.get_by_user_code_hash(hasher.hash(minted.user_code))
    assert by_device is not None and by_user is not None
    assert by_device.id == minted.authorization.id == by_user.id
    assert by_device.status == "pending"


# ── Must: approve binds tenant + user ────────────────────────────────────────


async def test_approve_binds_tenant_and_user(
    service: AgentOAuthService,
    repo: SqlAlchemyAgentOAuthRepository,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    minted = await _start(service)
    approved = await repo.approve(
        authorization_id=minted.authorization.id,
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
        now=_NOW,
    )
    assert approved.status == "approved"
    assert approved.tenant_id == tenant_user["tenant_id"]
    assert approved.user_id == tenant_user["user_id"]


# ── Must: deny ───────────────────────────────────────────────────────────────


async def test_deny_marks_denied(
    service: AgentOAuthService,
    repo: SqlAlchemyAgentOAuthRepository,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    minted = await _start(service)
    denied = await repo.deny(authorization_id=minted.authorization.id)
    assert denied.status == "denied"


# ── Must: mint is atomic + single-use ────────────────────────────────────────


async def test_mint_creates_one_token_and_consumes_authorization(
    service: AgentOAuthService,
    repo: SqlAlchemyAgentOAuthRepository,
    db_session: AsyncSession,
    hasher: Sha256SecretHasher,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    minted = await _start(service)
    await repo.approve(
        authorization_id=minted.authorization.id,
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
        now=_NOW,
    )
    token = await service.mint(
        authorization_id=minted.authorization.id,
        access_ttl_seconds=3600,
        refresh_ttl_seconds=2592000,
        now=_NOW,
    )
    assert token.access_token  # plaintext returned once
    assert token.token.tenant_id == tenant_user["tenant_id"]
    assert token.token.scope == minted.authorization.scope
    # exactly one token row, hashed
    cnt = await db_session.execute(text("SELECT count(*) FROM agent_tokens"))
    assert int(cnt.scalar_one()) == 1
    stored = await db_session.execute(
        text("SELECT access_token_hash FROM agent_tokens WHERE id = :i"), {"i": token.token.id}
    )
    assert stored.scalar_one() == hasher.hash(token.access_token)
    # authorization consumed in the same flow
    auth_status = await db_session.execute(
        text("SELECT status FROM device_authorizations WHERE id = :i"),
        {"i": minted.authorization.id},
    )
    assert auth_status.scalar_one() == "consumed"


# ── Must: resolve a valid token to its binding ───────────────────────────────


async def test_resolve_valid_access_token_returns_binding(
    service: AgentOAuthService,
    repo: SqlAlchemyAgentOAuthRepository,
    hasher: Sha256SecretHasher,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    minted = await _start(service)
    await repo.approve(
        authorization_id=minted.authorization.id,
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
        now=_NOW,
    )
    token = await service.mint(
        authorization_id=minted.authorization.id,
        access_ttl_seconds=3600,
        refresh_ttl_seconds=None,
        now=_NOW,
    )
    binding = await repo.resolve_access_token(
        access_token_hash=hasher.hash(token.access_token), now=_NOW + timedelta(seconds=10)
    )
    assert binding is not None
    assert binding.tenant_id == tenant_user["tenant_id"]
    assert binding.user_id == tenant_user["user_id"]
    assert binding.scope == "proxy"


# ── Must: expired/revoked token resolves to None (fail-closed) ───────────────


async def test_expired_token_resolves_to_none(
    service: AgentOAuthService,
    repo: SqlAlchemyAgentOAuthRepository,
    hasher: Sha256SecretHasher,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    minted = await _start(service)
    await repo.approve(
        authorization_id=minted.authorization.id,
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
        now=_NOW,
    )
    token = await service.mint(
        authorization_id=minted.authorization.id,
        access_ttl_seconds=3600,
        refresh_ttl_seconds=None,
        now=_NOW,
    )
    # now is AFTER the access token expiry
    binding = await repo.resolve_access_token(
        access_token_hash=hasher.hash(token.access_token), now=_NOW + timedelta(seconds=7200)
    )
    assert binding is None


async def test_unknown_token_resolves_to_none(
    repo: SqlAlchemyAgentOAuthRepository,
    hasher: Sha256SecretHasher,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    binding = await repo.resolve_access_token(
        access_token_hash=hasher.hash("never-minted"), now=_NOW
    )
    assert binding is None


async def test_revoked_token_resolves_to_none(
    service: AgentOAuthService,
    repo: SqlAlchemyAgentOAuthRepository,
    db_session: AsyncSession,
    hasher: Sha256SecretHasher,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    """A revoked token is fail-closed even though it is not expired (revocation control)."""
    minted = await _start(service)
    await repo.approve(
        authorization_id=minted.authorization.id,
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
        now=_NOW,
    )
    token = await service.mint(
        authorization_id=minted.authorization.id,
        access_ttl_seconds=3600,
        refresh_ttl_seconds=None,
        now=_NOW,
    )
    # revoke directly in the store, leaving the token UNEXPIRED
    await db_session.execute(
        text("UPDATE agent_tokens SET revoked_at = :t WHERE id = :i"),
        {"t": _NOW, "i": token.token.id},
    )
    await db_session.commit()
    binding = await repo.resolve_access_token(
        access_token_hash=hasher.hash(token.access_token), now=_NOW + timedelta(seconds=10)
    )
    assert binding is None  # revoked → fail-closed, despite being within the access TTL


# ── Must: token lookup never crosses tenants ─────────────────────────────────


async def test_token_lookup_is_tenant_isolated(
    service: AgentOAuthService,
    repo: SqlAlchemyAgentOAuthRepository,
    hasher: Sha256SecretHasher,
    make_tenant_user,
) -> None:
    a = await make_tenant_user("TenantA", "a@a.io")
    b = await make_tenant_user("TenantB", "b@b.io")

    ma = await _start(service)
    await repo.approve(
        authorization_id=ma.authorization.id,
        tenant_id=a["tenant_id"],
        user_id=a["user_id"],
        now=_NOW,
    )
    ta = await service.mint(
        authorization_id=ma.authorization.id,
        access_ttl_seconds=3600,
        refresh_ttl_seconds=None,
        now=_NOW,
    )

    mb = await _start(service)
    await repo.approve(
        authorization_id=mb.authorization.id,
        tenant_id=b["tenant_id"],
        user_id=b["user_id"],
        now=_NOW,
    )
    tb = await service.mint(
        authorization_id=mb.authorization.id,
        access_ttl_seconds=3600,
        refresh_ttl_seconds=None,
        now=_NOW,
    )

    binding_a = await repo.resolve_access_token(
        access_token_hash=hasher.hash(ta.access_token), now=_NOW
    )
    binding_b = await repo.resolve_access_token(
        access_token_hash=hasher.hash(tb.access_token), now=_NOW
    )
    assert binding_a is not None and binding_b is not None
    # each token resolves to its OWN tenant and never the other's (no cross-tenant leak)
    assert binding_a.tenant_id == a["tenant_id"]
    assert binding_b.tenant_id == b["tenant_id"]
    assert binding_a.tenant_id != binding_b.tenant_id
    assert binding_a.user_id == a["user_id"] and binding_b.user_id == b["user_id"]


# ── Reject: approve an expired authorization ─────────────────────────────────


async def test_reject_approve_expired_authorization(
    service: AgentOAuthService,
    repo: SqlAlchemyAgentOAuthRepository,
    db_session: AsyncSession,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    # ttl negative -> already expired at _NOW
    minted = await service.start_device_authorization(
        scope="proxy", interval_seconds=5, ttl_seconds=-1, now=_NOW
    )
    with pytest.raises(AuthorizationExpiredError) as exc:
        await repo.approve(
            authorization_id=minted.authorization.id,
            tenant_id=tenant_user["tenant_id"],
            user_id=tenant_user["user_id"],
            now=_NOW,
        )
    assert exc.value.code == "authorization_expired"
    # unchanged: still pending, unbound
    row = await db_session.execute(
        text("SELECT status, tenant_id FROM device_authorizations WHERE id = :i"),
        {"i": minted.authorization.id},
    )
    status, tid = row.one()
    assert status == "pending" and tid is None


# ── Reject: approve/deny a non-pending authorization ─────────────────────────


async def test_reject_approve_non_pending(
    service: AgentOAuthService,
    repo: SqlAlchemyAgentOAuthRepository,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    minted = await _start(service)
    await repo.deny(authorization_id=minted.authorization.id)  # now 'denied'
    with pytest.raises(AuthorizationNotPendingError) as exc:
        await repo.approve(
            authorization_id=minted.authorization.id,
            tenant_id=tenant_user["tenant_id"],
            user_id=tenant_user["user_id"],
            now=_NOW,
        )
    assert exc.value.code == "authorization_not_pending"


# ── Reject: mint from a non-approved authorization ───────────────────────────


async def test_reject_mint_non_approved(
    service: AgentOAuthService,
    db_session: AsyncSession,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    minted = await _start(service)  # still pending
    with pytest.raises(AuthorizationNotApprovedError) as exc:
        await service.mint(
            authorization_id=minted.authorization.id,
            access_ttl_seconds=3600,
            refresh_ttl_seconds=None,
            now=_NOW,
        )
    assert exc.value.code == "authorization_not_approved"
    cnt = await db_session.execute(text("SELECT count(*) FROM agent_tokens"))
    assert int(cnt.scalar_one()) == 0


# ── Reject: mint twice from the same authorization ───────────────────────────


async def test_reject_mint_twice(
    service: AgentOAuthService,
    repo: SqlAlchemyAgentOAuthRepository,
    db_session: AsyncSession,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    minted = await _start(service)
    await repo.approve(
        authorization_id=minted.authorization.id,
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
        now=_NOW,
    )
    await service.mint(
        authorization_id=minted.authorization.id,
        access_ttl_seconds=3600,
        refresh_ttl_seconds=None,
        now=_NOW,
    )
    with pytest.raises(AuthorizationAlreadyConsumedError) as exc:
        await service.mint(
            authorization_id=minted.authorization.id,
            access_ttl_seconds=3600,
            refresh_ttl_seconds=None,
            now=_NOW,
        )
    assert exc.value.code == "authorization_already_consumed"
    cnt = await db_session.execute(text("SELECT count(*) FROM agent_tokens"))
    assert int(cnt.scalar_one()) == 1  # no second token


# ── Reject: colliding pending authorization ──────────────────────────────────


async def test_reject_colliding_device_code(
    repo: SqlAlchemyAgentOAuthRepository,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    await repo.create_pending(
        device_code_hash="dch-collide",
        user_code_hash="uch-1",
        scope="proxy",
        interval_seconds=5,
        expires_at=_NOW + timedelta(seconds=600),
    )
    with pytest.raises(DeviceCodeCollisionError) as exc:
        await repo.create_pending(
            device_code_hash="dch-collide",  # same device_code hash
            user_code_hash="uch-2",
            scope="proxy",
            interval_seconds=5,
            expires_at=_NOW + timedelta(seconds=600),
        )
    assert exc.value.code == "device_code_collision"


async def test_reject_colliding_user_code_among_pending(
    repo: SqlAlchemyAgentOAuthRepository,
    tenant_user: dict[str, uuid.UUID],
) -> None:
    """The partial-unique index forbids two LIVE pending rows sharing a user_code."""
    await repo.create_pending(
        device_code_hash="dch-a",
        user_code_hash="uch-collide",
        scope="proxy",
        interval_seconds=5,
        expires_at=_NOW + timedelta(seconds=600),
    )
    with pytest.raises(DeviceCodeCollisionError) as exc:
        await repo.create_pending(
            device_code_hash="dch-b",  # different device code …
            user_code_hash="uch-collide",  # … but same pending user_code
            scope="proxy",
            interval_seconds=5,
            expires_at=_NOW + timedelta(seconds=600),
        )
    assert exc.value.code == "device_code_collision"
