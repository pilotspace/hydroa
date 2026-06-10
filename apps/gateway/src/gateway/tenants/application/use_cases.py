import uuid

from gateway.tenants.domain.entities import MIN_PASSWORD_LENGTH, Identity
from gateway.tenants.domain.errors import InvalidCredentialsError, WeakPasswordError
from gateway.tenants.domain.ports import IdentityRepository, PasswordHasher, TokenService


class SignupUseCase:
    def __init__(self, repository: IdentityRepository, hasher: PasswordHasher) -> None:
        self._repository = repository
        self._hasher = hasher

    async def execute(
        self, *, tenant_name: str, email: str, password: str
    ) -> tuple[uuid.UUID, uuid.UUID]:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError
        return await self._repository.create_tenant_with_owner(
            tenant_name=tenant_name,
            email=email.lower(),
            password_hash=self._hasher.hash(password),
        )


class LoginUseCase:
    def __init__(
        self, repository: IdentityRepository, hasher: PasswordHasher, tokens: TokenService
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._tokens = tokens

    async def execute(self, *, email: str, password: str) -> tuple[str, int]:
        user = await self._repository.get_user_by_email(email.lower())
        # verify() runs against a dummy hash when user is None, so both
        # failure paths cost the same time and return the same error.
        if not self._hasher.verify(user.password_hash if user else None, password) or user is None:
            raise InvalidCredentialsError
        return self._tokens.issue(
            user_id=user.id, tenant_id=user.tenant_id, role=user.role, email=user.email
        )


class GetIdentityUseCase:
    def __init__(self, tokens: TokenService) -> None:
        self._tokens = tokens

    def execute(self, token: str) -> Identity:
        return self._tokens.decode(token)
