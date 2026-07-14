"""Composite KeyAuthenticator — widens /v1 to accept both API keys and agent tokens.

Contract FROZEN @ v39 (agent-token-authn-seam TASK.md §3).

Dispatch grammar:
  raw_key starts with "sk-" → delegate to the wrapped SqlAlchemyKeyAuthenticator
                              (existing API-key path; byte-identical behavior).
  otherwise                 → candidate agent token: SHA-256 hash → resolve_access_token
                              → None = fail-closed (InvalidApiKeyError, same 401)
                              → AgentTokenBinding → AuthzResult(key_id=token_id,
                                monthly_budget_usd=settings.agent_oauth_default_budget_usd)

Security rules (enforced here):
  - Any resolve failure → InvalidApiKeyError → identical 401 (anti-enumeration preserved).
  - The raw token is NEVER logged.
  - `now` is server-owned: the caller cannot influence expiry evaluation.
  - The wrapped sk- path, the application use case, governance, billing, and the
    error catalog are UNCHANGED — this composite only widens accepted credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from gateway.agent_oauth.domain.entities import AgentTokenBinding
from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator

if TYPE_CHECKING:
    from gateway.core.config import Settings

# The grammar prefix that identifies an API key. Anything else is a candidate agent token.
_API_KEY_PREFIX = "sk-"


class CompositeKeyAuthenticator:
    """Implements the KeyAuthenticator Protocol; dispatches on the sk- grammar.

    Constructed per-request (session-scoped) so the underlying SQLAlchemy session
    and repository are never shared across requests (mirrors SqlAlchemyKeyAuthenticator).
    """

    def __init__(
        self,
        *,
        api_key_authenticator: SqlAlchemyKeyAuthenticator,
        agent_token_repo: SqlAlchemyAgentOAuthRepository,
        hasher: Sha256SecretHasher,
        settings: Settings | Any,
    ) -> None:
        self._api_key_authenticator = api_key_authenticator
        self._agent_token_repo = agent_token_repo
        self._hasher = hasher
        self._settings = settings

    async def authenticate(self, raw_key: str) -> AuthzResult:
        """Validate the raw Bearer key; return AuthzResult on success.

        Dispatch:
          sk- prefix → delegate to wrapped SqlAlchemyKeyAuthenticator (unchanged).
          else        → SHA-256 hash → resolve_access_token → AuthzResult or raise.

        Raises:
            InvalidApiKeyError: on ANY failure (malformed, unknown, expired, revoked).
                                 Byte-identical 401 for both credential classes
                                 (anti-enumeration: no distinguishing detail).
        """
        if raw_key.startswith(_API_KEY_PREFIX):
            # Existing API-key path — fully delegated, byte-identical.
            return await self._api_key_authenticator.authenticate(raw_key)

        # Agent token path — fail-closed.
        # `now` is server-owned; the caller cannot influence expiry evaluation.
        now = datetime.now(UTC)
        token_hash = self._hasher.hash(raw_key)
        binding: AgentTokenBinding | None = await self._agent_token_repo.resolve_access_token(
            access_token_hash=token_hash,
            now=now,
        )
        if binding is None:
            # Unknown / expired / revoked → identical 401 (no enumeration).
            raise InvalidApiKeyError

        return AuthzResult(
            tenant_id=binding.tenant_id,
            key_id=binding.token_id,
            monthly_budget_usd=self._settings.agent_oauth_default_budget_usd,
            # expires_at=None — resolve_access_token already gated expiry.
            # model_allowlist=None, rpm/tpm=None — defaults carry forward.
            # agent-identity-governance TASK.md §3 (FROZEN @ v1): additive — both stay
            # None for an unattached token (byte-identical to pre-task behavior).
            agent_principal_id=binding.principal_id,
            agent_principal_budget_usd=binding.principal_budget_usd,
        )
