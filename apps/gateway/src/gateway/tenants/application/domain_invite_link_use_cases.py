"""Use-cases for the domain-restricted shareable invite link (invite-by-domain TASK.md §3,
FROZEN @ v1, SECURITY, task 6a).

Five use-cases across the two surfaces:
  - CreateDomainInviteLinkUseCase   — eligibility gate + mint/supersede (admin).
  - ListDomainInviteLinksUseCase    — active links in the caller's tenant (admin).
  - RevokeDomainInviteLinkUseCase   — tenant-scoped revoke (admin).
  - IssueDomainRedemptionCodeUseCase — public step-1: resolve link, domain-match, arm a
    6-digit code (reused member_verify_code.py primitives), email it fail-open.
  - VerifyDomainRedemptionUseCase   — public step-2: one-transaction verify+provision under
    SELECT … FOR UPDATE (mirrors InviteRepository.accept via the repo choke point).

Sibling-consistency (invite_use_cases.py / invite_accept_use_cases.py): imports the concrete
repository directly — no new Protocol port.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from gateway.domain_capture.domain.domain_validation import normalize_domain
from gateway.domain_capture.domain.errors import DomainInvalidError
from gateway.domain_capture.domain.member_verify_code import (
    generate_member_verify_code,
    hash_member_verify_code,
    verify_member_verify_code,
)
from gateway.email.application.email_dispatch import send_email
from gateway.email.application.member_verify_code_email_template import (
    render_member_verify_code_email,
)
from gateway.email.domain.ports import EmailSender
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.tenants.domain.entities import MIN_PASSWORD_LENGTH, DomainInviteLink
from gateway.tenants.domain.errors import (
    DomainInviteDomainMismatchError,
    DomainInviteNotEligibleError,
    WeakPasswordError,
)
from gateway.tenants.infrastructure.argon2_hasher import Argon2PasswordHasher
from gateway.tenants.infrastructure.domain_invite_link_repository import (
    DomainInviteLinkRepository,
)

# Same opaque-token-hashed-at-rest scheme member-invite issuance/acceptance already use.
_token_hasher = Sha256SecretHasher()
# users.password_hash remains argon2 — a domain-link-joined member is indistinguishable
# from a signed-up owner or an invite-accepted user.
_password_hasher = Argon2PasswordHasher()


def _redeemer_email_domain(email: str) -> str | None:
    """Server-derived: normalize the domain of the redeemer-supplied email, IDENTICALLY to
    task-4's `_caller_email_domain` (subdomain/unicode/IP fail-closed). Returns None on any
    invalid host → the caller treats it as a domain mismatch (never a code, never a join).

    SECURITY (verify lens-B finding): the email MUST be a single well-formed addr-spec BEFORE
    the domain gate. A last-'@'-segment check alone would let `x@evil.com@acme.com` (two '@')
    or a CR/LF-laden local part pass while addressing a FOREIGN mailbox — the code would then
    be emailed off-domain. We fail CLOSED here, in the feature's OWN control, rather than lean
    on incidental MTA routing or stdlib CRLF hardening: exactly one '@', a non-empty local
    part, and no whitespace/control characters anywhere."""
    if email.count("@") != 1:
        return None
    local, _, _host = email.partition("@")
    if not local or any(ch.isspace() or ord(ch) < 32 for ch in email):
        return None
    try:
        return normalize_domain(email.rsplit("@", 1)[-1])
    except DomainInvalidError:
        return None


class CreateDomainInviteLinkUseCase:
    """Mint (or atomically supersede) an active link for a domain the caller's tenant is
    member/owner-verified on (M1/M2/M3)."""

    def __init__(self, repo: DomainInviteLinkRepository, *, link_ttl_days: int) -> None:
        self._repo = repo
        self._link_ttl_days = link_ttl_days

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        domain_raw: str,
        created_by_user_id: uuid.UUID,
        now: datetime,
    ) -> tuple[DomainInviteLink, str]:
        """Returns (link, plaintext_token). The token is returned exactly once.

        Raises:
            DomainInviteNotEligibleError: an invalid domain OR the tenant is not
                member/owner-verified on it (R2) — no link is minted.
        """
        try:
            domain = normalize_domain(domain_raw)
        except DomainInvalidError as exc:
            raise DomainInviteNotEligibleError(str(exc)) from None

        if not await self._repo.is_domain_eligible(tenant_id=tenant_id, domain=domain):
            raise DomainInviteNotEligibleError(
                f"Tenant {tenant_id} is not verified for domain {domain!r}"
            )

        token = secrets.token_urlsafe(32)
        token_hash = _token_hasher.hash(token)
        expires_at = now + timedelta(days=self._link_ttl_days)
        link = await self._repo.create_link(
            tenant_id=tenant_id,
            domain=domain,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by_user_id=created_by_user_id,
        )
        return link, token


class ListDomainInviteLinksUseCase:
    def __init__(self, repo: DomainInviteLinkRepository) -> None:
        self._repo = repo

    async def execute(self, *, tenant_id: uuid.UUID) -> list[DomainInviteLink]:
        return await self._repo.list_active(tenant_id=tenant_id)


class RevokeDomainInviteLinkUseCase:
    def __init__(self, repo: DomainInviteLinkRepository) -> None:
        self._repo = repo

    async def execute(
        self, *, link_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> DomainInviteLink:
        return await self._repo.revoke(link_id=link_id, tenant_id=tenant_id)


class IssueDomainRedemptionCodeUseCase:
    """Public step-1: resolve the active link, enforce the domain match, arm a fresh code
    bound to (link, email), and email it fail-open (M6/M8)."""

    def __init__(
        self,
        repo: DomainInviteLinkRepository,
        email_sender: EmailSender,
        *,
        jwt_secret: str,
        code_ttl_minutes: int,
        origin: str,
    ) -> None:
        self._repo = repo
        self._email_sender = email_sender
        self._jwt_secret = jwt_secret
        self._code_ttl_minutes = code_ttl_minutes
        self._origin = origin

    async def execute(self, *, token: str, email_raw: str, now: datetime) -> str:
        """Returns the normalized redeemer email (echoed in the 202).

        Raises:
            InviteNotFoundError / DomainInviteLinkInactiveError / InviteExpiredError:
                from the link resolution (R11/R9/R10).
            DomainInviteDomainMismatchError: the email's domain != the link's (R3) — NO code
                is issued and NO email is sent.
        """
        token_hash = _token_hasher.hash(token)
        link = await self._repo.resolve_active_link_by_token_hash(token_hash=token_hash, now=now)

        redeemer_domain = _redeemer_email_domain(email_raw)
        if redeemer_domain is None or redeemer_domain != link.domain:
            raise DomainInviteDomainMismatchError(
                "Email domain does not match the invite link's domain"
            )
        email = email_raw.strip().lower()

        code = generate_member_verify_code()
        code_hash = hash_member_verify_code(code, self._jwt_secret)
        code_expires_at = now + timedelta(minutes=self._code_ttl_minutes)
        # DB write COMMITS before the email is ever dispatched (the armed code must survive
        # an email outage — mirrors IssueMemberVerifyCodeUseCase).
        await self._repo.upsert_redemption(
            link_id=link.id,
            email=email,
            code_hash=code_hash,
            code_expires_at=code_expires_at,
        )
        # send_email is itself fail-open (never raises); awaited for deterministic capture.
        await send_email(
            self._email_sender,
            render_member_verify_code_email(
                to=email, domain=link.domain, code=code, origin=self._origin
            ),
        )
        return email


class VerifyDomainRedemptionUseCase:
    """Public step-2: verify the code and provision exactly ONE MEMBER, all in one
    transaction under SELECT … FOR UPDATE (M7). Exactly-once under concurrency: the link row
    lock serializes racing verifies; the consumed redemption row makes the loser see an
    invalid code."""

    def __init__(
        self,
        repo: DomainInviteLinkRepository,
        *,
        jwt_secret: str,
        max_attempts: int,
    ) -> None:
        self._repo = repo
        self._jwt_secret = jwt_secret
        self._max_attempts = max_attempts

    async def execute(
        self, *, token: str, email_raw: str, code: str, password: str, now: datetime
    ) -> tuple[uuid.UUID, uuid.UUID, str]:
        """Returns (tenant_id, user_id, email) of the provisioned MEMBER.

        Ordering (fail-closed): cheap no-DB password-strength check FIRST (so a weak
        password never consumes/increments the code, R7), then the FOR-UPDATE transaction.

        Raises: WeakPasswordError · InviteNotFoundError · DomainInviteLinkInactiveError ·
            InviteExpiredError · DomainInviteDomainMismatchError · MemberVerifyCodeInvalidError
            · MemberVerifyCodeExpiredError · MemberVerifyTooManyAttemptsError ·
            SeatCapExceededError · EmailAlreadyRegisteredError.
        """
        from gateway.domain_capture.domain.errors import (
            MemberVerifyCodeExpiredError,
            MemberVerifyCodeInvalidError,
            MemberVerifyTooManyAttemptsError,
        )
        from gateway.tenants.domain.errors import (
            DomainInviteDomainMismatchError,
            DomainInviteLinkInactiveError,
            InviteExpiredError,
            InviteNotFoundError,
        )
        from gateway.tenants.domain.entities import DomainInviteLinkStatus

        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError

        email = email_raw.strip().lower()
        token_hash = _token_hasher.hash(token)

        state = await self._repo.load_link_and_redemption_for_update(
            token_hash=token_hash, email=email
        )
        # R11: unknown token — no oracle.
        if state is None:
            raise InviteNotFoundError("No domain invite link for the given token")
        # R9: revoked/superseded link.
        if state.status != DomainInviteLinkStatus.ACTIVE:
            raise DomainInviteLinkInactiveError(f"Domain invite link {state.link_id} is not active")
        # R10: link past expiry.
        if state.link_expires_at < now:
            raise InviteExpiredError(f"Domain invite link {state.link_id} expired")
        # R3: the redeemer's email must belong to the link's domain (fail-closed).
        redeemer_domain = _redeemer_email_domain(email)
        if redeemer_domain is None or redeemer_domain != state.domain:
            raise DomainInviteDomainMismatchError(
                "Email domain does not match the invite link's domain"
            )

        # No in-flight code for (link, email): cannot verify (consumed / never issued / a
        # winner already provisioned under concurrency — the loser lands here). R4/R14.
        if state.redemption_id is None or state.code_hash is None:
            raise MemberVerifyCodeInvalidError
        # R6: expired code — clear it (single-use) and raise.
        if state.code_expires_at is None or now > state.code_expires_at:
            await self._repo.invalidate_redemption(redemption_id=state.redemption_id)
            raise MemberVerifyCodeExpiredError

        if verify_member_verify_code(
            code=code, code_hash=state.code_hash, jwt_secret=self._jwt_secret
        ):
            # Correct code — provision + consume in the SAME still-locked transaction.
            return await self._repo.provision_member_and_consume(
                tenant_id=state.tenant_id,
                redemption_id=state.redemption_id,
                email=email,
                password_hash=_password_hasher.hash(password),
                now=now,
            )

        # R4/R5: wrong code — persist the increment (invalidate at the cap) BEFORE raising.
        new_count = state.attempt_count + 1
        invalidate = new_count >= self._max_attempts
        await self._repo.bump_redemption_attempt(
            redemption_id=state.redemption_id, invalidate=invalidate
        )
        if invalidate:
            raise MemberVerifyTooManyAttemptsError
        raise MemberVerifyCodeInvalidError
