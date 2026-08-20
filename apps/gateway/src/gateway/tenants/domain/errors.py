from __future__ import annotations

import uuid


class IdentityError(Exception):
    """Base for tenant-identity domain failures."""


class EmailAlreadyRegisteredError(IdentityError):
    pass


class WeakPasswordError(IdentityError):
    pass


class IndividualPlanMissingError(IdentityError):
    """account-type-discriminator TASK.md §3 (FROZEN @ v1, R3), repointed by
    plan-tiers-and-base-fee TASK.md §3 M3 to the `free` plan (class NAME unchanged —
    contractually reused): a personal signup demands the seeded `free` plan, but it is
    absent (seed migration not run). Translated by the signup router to the
    SIGNUP_PLAN_UNPROVISIONED 500 so a personal tenant is NEVER silently left unplanned.
    Fail-closed, never a partial."""

    pass


class InvalidCredentialsError(IdentityError):
    pass


class InvalidTokenError(IdentityError):
    pass


class UserNotFoundError(IdentityError):
    """Target user does not exist in the caller's tenant."""

    pass


class EscalationForbiddenError(IdentityError):
    """Caller attempted to assign a role above their own privilege ceiling."""

    pass


class InviteNotFoundError(IdentityError):
    """Invite does not exist in the caller's tenant (unknown id OR belongs to a different
    tenant — deliberately indistinguishable; see member-invite-issuance TASK.md R8)."""

    pass


class InviteNotPendingError(IdentityError):
    """Invite exists but its status is not 'pending' (already accepted or already revoked)."""

    pass


class InviteExpiredError(IdentityError):
    """Invite resolved and its status IS 'pending', but expires_at has passed
    (member-invite-acceptance TASK.md §3) — a computed check, never a persisted 4th status."""

    pass


class InviteEmailAlreadyMemberError(IdentityError):
    """Target email already belongs to an existing user in the caller's own tenant."""

    pass


class LastBillingOwnerError(IdentityError):
    """Target is the tenant's CURRENT billing_owner_user_id and the write would leave the
    tenant with zero billing-capable owners (billing-owner-of-record TASK.md §3 M2/M3) —
    raised by AssignUserRoleUseCase.execute (HOOK 1, role-change) and
    SqlAlchemyScimUserRepository.set_active (HOOK 2, deactivation, via
    SetScimUserActiveUseCase.execute)."""

    pass


class BillingOwnerIneligibleError(IdentityError):
    """PUT /admin/billing-owner target is not an ACTIVE, billing-capable
    ({OWNER, BILLING_ADMIN}) member of the caller's own tenant (billing-owner-of-record
    TASK.md §3 M5) — raised by ReassignBillingOwnerUseCase.execute."""

    pass


class DomainInviteNotEligibleError(IdentityError):
    """POST /admin/domain-invite-links for a domain the caller's tenant is NOT member/owner-
    verified on (invite-by-domain TASK.md §3 M1/R2) — no link is minted. Tenant-scoped:
    eligibility is only ever read for the caller's OWN tenant (anti-confused-deputy)."""

    pass


class DomainInviteDomainMismatchError(IdentityError):
    """The redeemer-supplied email's domain != the link's domain (invite-by-domain TASK.md
    §3 M6/R3), normalized identically to task-4's `_caller_email_domain` (subdomain/unicode/
    IP fail-closed). At step-1 no code is emailed; a code proves control of a mailbox and
    nothing else."""

    pass


class DomainInviteLinkInactiveError(IdentityError):
    """The domain invite link resolved but its status is not 'active' — revoked (or
    superseded) mid-flight (invite-by-domain TASK.md §3 M5/R9). A revoked link can never
    again be redeemed."""

    pass


class SeatCapExceededError(IdentityError):
    """Admitting one more active member would meet-or-exceed the tenant's effective seat
    cap (plan-seat-cap TASK.md §3 M2, FROZEN @ v1). Carries structured data — unlike every
    other plain-marker IdentityError above — because its 5 call sites span TWO
    incompatible error envelopes (RFC 9457 and RFC 7644) and each must build its OWN
    shape without a second query."""

    def __init__(
        self, *, plan_id: uuid.UUID, plan_name: str, seat_cap: int, current_seats: int
    ) -> None:
        super().__init__(
            f"Seat cap exceeded: plan={plan_name!r} seat_cap={seat_cap} "
            f"current_seats={current_seats}"
        )
        self.plan_id = plan_id
        self.plan_name = plan_name
        self.seat_cap = seat_cap
        self.current_seats = current_seats


class PendingSignupNotFoundError(IdentityError):
    """POST /admin/auth/signup/confirm — token unknown, already consumed, or never issued
    (scoped-self-serve-signup TASK.md §3 M11/R5, FROZEN @ v1, SECURITY). Deliberately a
    plain marker — no distinguishing detail beyond this class, mirroring
    InviteNotFoundError's own indistinguishable-by-design shape."""

    pass


class PendingSignupExpiredError(IdentityError):
    """POST /admin/auth/signup/confirm — a matching pending_personal_signups row existed
    but its expires_at had passed (scoped-self-serve-signup TASK.md §3 M11/R6, FROZEN @
    v1, SECURITY). The row is deleted as cleanup by the caller before this is raised."""

    pass


class PasswordResetInvalidError(IdentityError):
    """POST /admin/auth/password-reset/confirm — token unknown or already consumed
    (auth-hardening-login-sessions TASK.md §3 M3/E5, FROZEN @ v1, SECURITY). Distinct
    from expired per A11: the caller possesses the emailed token, so R-sec-6 makes
    distinguishing invalid/expired safe (the pending-signup precedent)."""

    pass


class PasswordResetExpiredError(IdentityError):
    """POST /admin/auth/password-reset/confirm — a matching token row existed but its
    expires_at is at/before now (auth-hardening-login-sessions TASK.md §3 A5/E6, fail
    closed at the boundary instant)."""

    pass


class SessionRevocationUnavailableError(IdentityError):
    """The session-revocation store could not answer within its bound
    (auth-hardening-login-sessions TASK.md §3 M6/E11, FROZEN @ v1, SECURITY).

    Fail-CLOSED marker: callers translate this to the 503-class ERR_AUTH_UNAVAILABLE —
    NEVER to a silent allow (a revocation decision, not an availability gate; mirrors
    DbImpersonationSessionGuard's fail-closed convention) and NEVER to a 401 (the
    caller's token is not known-bad; telling them it is would be a lie)."""

    pass
