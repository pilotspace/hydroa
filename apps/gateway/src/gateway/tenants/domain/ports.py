import uuid
from typing import Protocol

from gateway.tenants.domain.entities import Identity, Role, User


class IdentityRepository(Protocol):
    async def create_tenant_with_owner(
        self, *, tenant_name: str, email: str, password_hash: str
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Create tenant + owner atomically; returns (tenant_id, user_id).

        Raises EmailAlreadyRegisteredError; on any failure neither row persists.
        """
        ...

    async def get_user_by_email(self, email: str) -> User | None: ...

    async def get_or_provision_oidc_user(
        self,
        *,
        email: str,
        tenant_id: uuid.UUID,
        password_hash: str,
    ) -> User:
        """Get existing user by email OR create with role=member if absent.

        Raises OidcTenantConflictError if the user exists bound to a different tenant_id.
        The provisioned user always has role=member regardless of any claims.
        """
        ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str | None, password: str) -> bool:
        """False on mismatch or None hash; MUST cost the same time either way
        (no user enumeration through timing)."""
        ...


class TokenService(Protocol):
    def issue(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: Role, email: str
    ) -> tuple[str, int]:
        """Returns (signed token, expires_in seconds)."""
        ...

    def decode(self, token: str) -> Identity:
        """Raises InvalidTokenError on any failure (signature, expiry, issuer, shape)."""
        ...
