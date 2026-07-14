"""Application use cases for agent principals.

Contract (agent-identity-governance TASK.md §3 — FROZEN @ v1):
  RBAC ceiling {OWNER, ADMIN} for create/attach/detach/kill (ForbiddenError ->
  router maps to AUTH_FORBIDDEN, 403). M5 (list) is readable by ANY authenticated
  tenant role — no RBAC gate on ListAgentPrincipalsUseCase.

Every tenant-scoped repository call filters tenant_id in the SAME query as its
existence check (SqlAlchemyAgentOAuthRepository's attach_token/detach_token/
kill_principal) — this layer never does a separate lookup-then-scope-check.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.agent_oauth.domain.entities import AgentPrincipal
from gateway.agent_oauth.domain.ports import AgentOAuthRepository
from gateway.agent_oauth.infrastructure.orm import AgentPrincipalRow, AgentTokenRow
from gateway.keys.domain.errors import ForbiddenError
from gateway.tenants.domain.entities import Role

_log = logging.getLogger(__name__)


def _ensure_owner_or_admin(role: Role) -> None:
    """RBAC ceiling for create/attach/detach/kill (TASK.md §3, CONFIRMED at freeze).

    Narrower than KEYS_MANAGE-holding roles (which include OPERATOR) — a kill
    switch's tenant-wide blast radius reads as categorically more destructive than
    revoking a single key.
    """
    if role not in (Role.OWNER, Role.ADMIN):
        raise ForbiddenError


class CreateAgentPrincipalUseCase:
    """M1: create a named agent principal, tenant-scoped to the caller."""

    def __init__(self, repository: AgentOAuthRepository) -> None:
        self._repo = repository

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        role: Role,
        name: str,
        owner_user_id: uuid.UUID | None,
        monthly_budget_usd: Decimal | None,
        rpm_limit: int | None,
        tpm_limit: int | None,
    ) -> AgentPrincipal:
        _ensure_owner_or_admin(role)
        return await self._repo.create_principal(
            tenant_id=tenant_id,
            name=name,
            owner_user_id=owner_user_id,
            monthly_budget_usd=monthly_budget_usd,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
        )


class ListAgentPrincipalsUseCase:
    """M5: any authenticated tenant role may list — no RBAC gate."""

    def __init__(self, repository: AgentOAuthRepository) -> None:
        self._repo = repository

    async def execute(self, *, tenant_id: uuid.UUID) -> list[AgentPrincipal]:
        return await self._repo.list_principals(tenant_id=tenant_id)


class AttachAgentTokenUseCase:
    """M2: attach an existing, already-minted agent_tokens row to a principal."""

    def __init__(self, repository: AgentOAuthRepository) -> None:
        self._repo = repository

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        role: Role,
        principal_id: uuid.UUID,
        token_id: uuid.UUID,
    ) -> None:
        _ensure_owner_or_admin(role)
        await self._repo.attach_token(
            principal_id=principal_id, token_id=token_id, tenant_id=tenant_id
        )


class DetachAgentTokenUseCase:
    """M3: detach a token — reverts to tenant-wide default governance."""

    def __init__(self, repository: AgentOAuthRepository) -> None:
        self._repo = repository

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        role: Role,
        principal_id: uuid.UUID,
        token_id: uuid.UUID,
    ) -> None:
        _ensure_owner_or_admin(role)
        await self._repo.detach_token(
            principal_id=principal_id, token_id=token_id, tenant_id=tenant_id
        )


class KillAgentPrincipalUseCase:
    """M7/M8: kill a principal — atomic + idempotent (returns just_killed for the
    caller to decide whether to schedule the audit event / bulk-revoke already ran)."""

    def __init__(self, repository: AgentOAuthRepository) -> None:
        self._repo = repository

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        role: Role,
        principal_id: uuid.UUID,
        now: datetime,
    ) -> tuple[AgentPrincipal, bool]:
        _ensure_owner_or_admin(role)
        return await self._repo.kill_principal(
            principal_id=principal_id, tenant_id=tenant_id, now=now
        )


async def bump_principal_last_seen(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    token_id: uuid.UUID,
    now: datetime,
) -> None:
    """M6: fire-and-forget last_seen_at bump for a principal-attached token's mint.

    Mirrors record_audit's fail-open, separate-session shape — a write failure here
    NEVER changes the mint's own HTTP response. Scheduled via asyncio.ensure_future()
    from token_router.py's mint-success path, never awaited on the hot path.

    A brand-new mint's token is unattached by construction (a principal can only be
    attached to an ALREADY-MINTED agent_tokens row — TASK.md §0 Ground note #6, which
    closes the attach-vs-in-flight-poll race), so this is a guaranteed no-op (0 rows
    matched) for the ordinary mint path today; it is wired now so the mint-tied-to-an-
    attached-token contract (§3 M6) holds for any token that already carries a
    principal_id at mint-commit time, without a second, hot-path write.
    """
    try:
        async with session_factory() as session:
            await session.execute(
                update(AgentPrincipalRow)
                .where(
                    AgentPrincipalRow.id.in_(
                        select(AgentTokenRow.principal_id).where(
                            AgentTokenRow.id == token_id,
                            AgentTokenRow.principal_id.is_not(None),
                        )
                    )
                )
                .values(last_seen_at=now)
            )
            await session.commit()
    except Exception as exc:
        _log.warning(
            "bump_principal_last_seen: failed to persist (swallowed — fail-open)",
            exc_info=exc,
            extra={"token_id": str(token_id)},
        )
