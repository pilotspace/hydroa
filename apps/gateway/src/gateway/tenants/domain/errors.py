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
