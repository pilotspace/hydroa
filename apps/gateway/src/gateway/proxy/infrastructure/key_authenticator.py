"""Infrastructure adapter: KeyAuthenticator → delegates to AuthzUseCase."""

from __future__ import annotations

from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult


class SqlAlchemyKeyAuthenticator:
    """Bridges the proxy domain port to the keys application AuthzUseCase.

    A new instance is created per-request (session-scoped) so the underlying
    SQLAlchemy session is never shared across requests.
    """

    def __init__(self, authz_use_case: AuthzUseCase) -> None:
        self._use_case = authz_use_case

    async def authenticate(self, raw_key: str) -> AuthzResult:
        """Validate the raw Bearer key.

        Delegates entirely to AuthzUseCase — no duplication of hashing logic.
        Raises InvalidApiKeyError on any failure.
        """
        return await self._use_case.execute(raw_key)
