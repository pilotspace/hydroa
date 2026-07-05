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
