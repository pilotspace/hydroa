"""Use-cases for tenant invite issuance (member-invite-issuance TASK.md §3).

Provides:
  - CreateInviteUseCase: POST /admin/invites — create/re-invite (MEMBERS_MANAGE + ceiling).
  - ListPendingInvitesUseCase: GET /admin/invites — list pending invites in caller's tenant.
  - RevokeInviteUseCase: DELETE /admin/invites/{id} — revoke a pending invite.

These are pure application-layer objects; all security logic lives here (server-side
enforcement per the frozen contract), mirroring users_use_cases.py's shape. The router
calls them after MEMBERS_MANAGE gating.

Note on CreateInviteUseCase.execute's return type: §3 CONTRACT's prose signature reads
``-> Invite``, but the frozen ``Invite`` dataclass deliberately excludes the plaintext
token (token_hash is infra-only, and the plaintext is NEVER persisted at all, M3) while M3
also requires the plaintext to reach the 201 body exactly once. The only way to satisfy
both frozen requirements is for this use case to return the plaintext alongside the Invite
— hence ``-> tuple[Invite, str]`` below. This is a contract-prose gap (the signature line
is inconsistent with the Invite dataclass + M3 in the SAME frozen document), not a product
ambiguity requiring a judgment call: no other shape lets the router assemble the required
201 body. Flagged in the build report as a spec-delta rather than silently edited into
TASK.md (which stays untouched).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta

from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.tenants.application.users_use_cases import assert_role_within_ceiling
from gateway.tenants.domain.entities import Invite, Role
from gateway.tenants.domain.errors import InviteEmailAlreadyMemberError
from gateway.tenants.infrastructure.invite_repository import InviteRepository

# §1 ⚠ lowest-confidence assumption: 7-day expiry is a HARDCODED default — accepted
# as-drafted at freeze (TASK.md §3 Status line); a one-line constant change if Tin later
# wants a different number, never a contract-shape change (no request field exposes a
# caller-supplied TTL either way).
INVITE_EXPIRY = timedelta(days=7)

# 32-byte CSPRNG opaque value — the codebase's existing, consistent pattern (agent_oauth
# device/access/refresh codes, API-key secrets, OIDC state tokens all generate this way, M3).
_TOKEN_BYTES = 32

_hasher = Sha256SecretHasher()


class CreateInviteUseCase:
    """Create (or atomically re-invite/supersede) a pending invite.

    Invariants (all enforced SERVER-SIDE):
      - ESCALATION CEILING: SAME shared predicate as AssignUserRoleUseCase (M1/R5), which
        ALSO unconditionally rejects target_role == SUPERADMIN (defense-in-depth behind
        the router's own 422 pre-check, M2/R4).
      - Anti-enumeration: never queries outside the caller's own tenant (M6).
      - CALLER-tenant email collision -> InviteEmailAlreadyMemberError (R7/M7).
      - Re-invite atomically supersedes any existing pending row (M5).
    """

    def __init__(self, repo: InviteRepository) -> None:
        self._repo = repo

    async def execute(
        self,
        *,
        caller_user_id: uuid.UUID,
        caller_role: Role,
        tenant_id: uuid.UUID,
        email: str,
        role: Role,
        now: datetime,
    ) -> tuple[Invite, str]:
        """Returns (Invite, plaintext_token) — the plaintext is NEVER persisted (M3)."""
        assert_role_within_ceiling(caller_role=caller_role, target_role=role)

        normalized_email = email.lower()

        # Tenant-SCOPED ONLY — never cross-tenant (M6 anti-enumeration).
        if await self._repo.user_exists_in_tenant(tenant_id=tenant_id, email=normalized_email):
            raise InviteEmailAlreadyMemberError(
                f"{normalized_email} already belongs to a member of this tenant"
            )

        token = secrets.token_urlsafe(_TOKEN_BYTES)
        token_hash = _hasher.hash(token)
        expires_at = now + INVITE_EXPIRY

        invite = await self._repo.create_or_replace(
            tenant_id=tenant_id,
            email=normalized_email,
            role=role,
            token_hash=token_hash,
            expires_at=expires_at,
            invited_by_user_id=caller_user_id,
        )
        return invite, token


class ListPendingInvitesUseCase:
    """Return all pending invites in the caller's tenant (M8)."""

    def __init__(self, repo: InviteRepository) -> None:
        self._repo = repo

    async def execute(self, *, tenant_id: uuid.UUID) -> list[Invite]:
        return await self._repo.list_pending_by_tenant(tenant_id=tenant_id)


class RevokeInviteUseCase:
    """Revoke a pending invite — tenant-scoped, not creator-scoped (M9)."""

    def __init__(self, repo: InviteRepository) -> None:
        self._repo = repo

    async def execute(self, *, invite_id: uuid.UUID, tenant_id: uuid.UUID) -> Invite:
        return await self._repo.revoke(invite_id=invite_id, tenant_id=tenant_id)
