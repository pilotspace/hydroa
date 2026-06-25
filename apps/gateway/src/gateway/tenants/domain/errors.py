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
