from __future__ import annotations

import uuid


class IdentityError(Exception):
    """Base for tenant-identity domain failures."""


class EmailAlreadyRegisteredError(IdentityError):
    pass


class WeakPasswordError(IdentityError):
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
