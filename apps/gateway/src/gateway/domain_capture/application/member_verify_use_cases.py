"""Member-verified recognition use-cases (member-verified-recognition TASK.md §3/§5 —
FROZEN @ v1, SECURITY).

Three use-cases:
  - IssueMemberVerifyCodeUseCase   — signup-time best-effort issuance (fail-OPEN at the
    caller/router boundary): create/reissue the pending DNS claim, arm a fresh 6-digit code
    (keyed hash + ~15-min expiry, attempt_count=0), email it to the owner's OWN signup
    address. Generic/public domains are silently skipped (R-sec-5).
  - VerifyMemberCodeUseCase        — reads the claim row under SELECT … FOR UPDATE, enforces
    owner (router) + M6b domain-match, then constant-time-compares the submitted code and
    either flips member_verified_at (single guarded write) or increments the attempt counter
    (invalidating at the ≤5 cap). status/verified_at/indexes are NEVER written (M7).
  - ResendMemberVerifyCodeUseCase  — re-issues a fresh code for a pending, non-generic,
    business claim whose domain matches the caller's own email domain.

The keyed hash (Option A) + the recipient/domain being server-derived are the security core.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from gateway.domain_capture.application.create_claim_use_case import CreateDomainClaimUseCase
from gateway.domain_capture.domain.domain_validation import normalize_domain
from gateway.domain_capture.domain.entities import ClaimStatus, DomainClaim
from gateway.domain_capture.domain.errors import (
    DomainClaimNotFoundError,
    DomainClaimNotPendingError,
    DomainGenericError,
    MemberVerifyCodeExpiredError,
    MemberVerifyCodeInvalidError,
    MemberVerifyDomainMismatchError,
    MemberVerifyNotEligibleError,
    MemberVerifyTooManyAttemptsError,
)
from gateway.domain_capture.domain.member_verify_code import (
    generate_member_verify_code,
    hash_member_verify_code,
    verify_member_verify_code,
)
from gateway.domain_capture.domain.ports import DomainClaimRepository
from gateway.domain_capture.domain.public_email_domains import is_public_email_domain
from gateway.email.application.email_dispatch import send_email
from gateway.email.application.member_verify_code_email_template import (
    render_member_verify_code_email,
)
from gateway.email.domain.ports import EmailSender


def _caller_email_domain(caller_email: str) -> str:
    """Server-derived: normalize the domain of the AUTHENTICATED caller's own email."""
    return normalize_domain(caller_email.rsplit("@", 1)[-1])


class IssueMemberVerifyCodeUseCase:
    """Best-effort signup-time issuance. The CALLER (signup router) wraps `execute` in a
    fail-OPEN try/except so signup always returns 201 (M2, R-fail-open)."""

    def __init__(
        self,
        create_claim_use_case: CreateDomainClaimUseCase,
        repository: DomainClaimRepository,
        email_sender: EmailSender,
        *,
        jwt_secret: str,
        code_ttl_seconds: int,
        origin: str,
    ) -> None:
        self._create_claim = create_claim_use_case
        self._repository = repository
        self._email_sender = email_sender
        self._jwt_secret = jwt_secret
        self._code_ttl_seconds = code_ttl_seconds
        self._origin = origin

    async def execute(
        self, *, tenant_id: uuid.UUID, domain: str, created_by_user_id: uuid.UUID, recipient_email: str
    ) -> None:
        norm = normalize_domain(domain)
        # R-sec-5: generic/public providers never get a code (silent skip — signup 201).
        if is_public_email_domain(norm):
            return

        # Create/reissue the pending DNS claim (fresh challenge), then arm the code.
        claim = await self._create_claim.execute(
            tenant_id=tenant_id, domain_raw=norm, created_by_user_id=created_by_user_id
        )
        code = generate_member_verify_code()
        code_hash = hash_member_verify_code(code, self._jwt_secret)
        expires_at = datetime.now(UTC) + timedelta(seconds=self._code_ttl_seconds)
        # DB write COMMITS before the email is ever dispatched (M2: the hashed code must
        # survive an email outage).
        await self._repository.issue_member_verify_code(
            claim_id=claim.id, tenant_id=tenant_id, code_hash=code_hash, expires_at=expires_at
        )
        message = render_member_verify_code_email(
            to=recipient_email, domain=norm, code=code, origin=self._origin
        )
        # send_email is itself fail-open (never raises); awaited here for deterministic
        # capture. The whole execute() is additionally wrapped fail-open by the router.
        await send_email(self._email_sender, message)


class VerifyMemberCodeUseCase:
    def __init__(
        self,
        repository: DomainClaimRepository,
        *,
        jwt_secret: str,
        max_attempts: int,
    ) -> None:
        self._repository = repository
        self._jwt_secret = jwt_secret
        self._max_attempts = max_attempts

    async def execute(
        self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID, code: str, caller_email: str
    ) -> DomainClaim:
        # SELECT … FOR UPDATE — holds the row-lock on this session until the terminal
        # mark/bump commits, so concurrent guesses serialize and the ≤5 cap is EXACT.
        state = await self._repository.load_member_verify_row_for_update(
            claim_id=claim_id, tenant_id=tenant_id
        )
        # R1: unknown OR cross-tenant — indistinguishable.
        if state is None:
            raise DomainClaimNotFoundError
        # R8: the stronger DNS rung already holds — nothing to member-verify.
        if state.status == ClaimStatus.VERIFIED:
            raise DomainClaimNotPendingError
        # M6b / R10: the caller's mailbox proof can only cover its OWN domain.
        if state.domain != _caller_email_domain(caller_email):
            raise MemberVerifyDomainMismatchError

        # No in-flight code (consumed / invalidated / never issued): cannot verify.
        if state.code_hash is None:
            raise MemberVerifyCodeInvalidError
        # R4: expired — clear the code (single-use), must resend.
        now = datetime.now(UTC)
        if state.code_expires_at is None or now > state.code_expires_at:
            await self._repository.bump_member_verify_attempt(claim_id=claim_id, invalidate=True)
            raise MemberVerifyCodeExpiredError

        if verify_member_verify_code(
            code=code, code_hash=state.code_hash, jwt_secret=self._jwt_secret
        ):
            # Success: single guarded write — set member_verified_at, clear the code.
            return await self._repository.mark_member_verified(claim_id=claim_id)

        # R3/R5: wrong code — atomic increment; invalidate at the cap.
        new_count = state.attempt_count + 1
        invalidate = new_count >= self._max_attempts
        await self._repository.bump_member_verify_attempt(
            claim_id=claim_id, invalidate=invalidate
        )
        if invalidate:
            raise MemberVerifyTooManyAttemptsError
        raise MemberVerifyCodeInvalidError


class ResendMemberVerifyCodeUseCase:
    def __init__(
        self,
        repository: DomainClaimRepository,
        email_sender: EmailSender,
        *,
        jwt_secret: str,
        code_ttl_seconds: int,
        origin: str,
    ) -> None:
        self._repository = repository
        self._email_sender = email_sender
        self._jwt_secret = jwt_secret
        self._code_ttl_seconds = code_ttl_seconds
        self._origin = origin

    async def execute(
        self,
        *,
        claim_id: uuid.UUID,
        tenant_id: uuid.UUID,
        caller_email: str,
        account_type: str | None,
    ) -> DomainClaim:
        claim = await self._repository.get_own(claim_id=claim_id, tenant_id=tenant_id)
        # R1: unknown OR cross-tenant — indistinguishable.
        if claim is None:
            raise DomainClaimNotFoundError
        # R8: already DNS-verified — the stronger rung holds.
        if claim.status == ClaimStatus.VERIFIED:
            raise DomainClaimNotPendingError
        # M6b / R10: the caller's mailbox proof can only cover its OWN domain.
        if claim.domain != _caller_email_domain(caller_email):
            raise MemberVerifyDomainMismatchError
        # R6 / R-sec-5: generic/public provider — never member-verifiable.
        if is_public_email_domain(claim.domain):
            raise DomainGenericError
        # R7 / R-sec-5: personal accounts never get member-verified.
        if account_type == "personal":
            raise MemberVerifyNotEligibleError

        code = generate_member_verify_code()
        code_hash = hash_member_verify_code(code, self._jwt_secret)
        expires_at = datetime.now(UTC) + timedelta(seconds=self._code_ttl_seconds)
        updated = await self._repository.issue_member_verify_code(
            claim_id=claim_id, tenant_id=tenant_id, code_hash=code_hash, expires_at=expires_at
        )
        message = render_member_verify_code_email(
            to=caller_email, domain=claim.domain, code=code, origin=self._origin
        )
        await send_email(self._email_sender, message)
        return updated
