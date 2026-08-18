"""Dependency providers for keys API endpoints."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_FORBIDDEN,
    AUTH_TOKEN_INVALID,
    AUTH_TOKEN_MISSING,
    AUTH_UNAVAILABLE,
)
from gateway.keys.application.use_cases import (
    AuthzUseCase,
    CreateKeyUseCase,
    ListKeysUseCase,
    RevokeKeyUseCase,
    RotateKeyUseCase,
    UpdateKeyUseCase,
)
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.infrastructure.composite_key_authenticator import CompositeKeyAuthenticator
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator
from gateway.tenants.domain.authz import (
    ROLE_PERMISSIONS,
    Permission,
    ensure_impersonation_session_live,
    ensure_session_not_revoked,
)
from gateway.tenants.domain.entities import Identity
from gateway.tenants.domain.errors import InvalidTokenError, SessionRevocationUnavailableError
from gateway.tenants.domain.ports import TokenService
from gateway.tenants.infrastructure.impersonation_session_guard import DbImpersonationSessionGuard
from gateway.tenants.infrastructure.session_revocation import DbSessionRevocationGuard

# Singleton hasher — stateless, safe to share
_hasher = Sha256SecretHasher()


def get_token_service(request: Request) -> TokenService:
    tokens: TokenService = request.app.state.token_service
    return tokens


def get_bearer_token(request: Request) -> str:
    """Extract raw Bearer token from Authorization header."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AUTH_TOKEN_MISSING.exc()
    return token


async def get_identity(
    request: Request,
    token: Annotated[str, Depends(get_bearer_token)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Identity:
    """Decode JWT and return the caller's Identity; raise 401 on any failure.

    impersonation-live-session-guard TASK.md §3 Part D.2 — call site 2/5.
    """
    try:
        identity = tokens.decode(token)
        await ensure_impersonation_session_live(
            identity,
            DbImpersonationSessionGuard(
                session=session,
                timeout_seconds=request.app.state.settings.impersonation_live_check_timeout_seconds,
            ),
        )
        # auth-hardening-login-sessions TASK.md §3 M5 — revocation call site 2/5.
        await ensure_session_not_revoked(
            identity,
            DbSessionRevocationGuard(
                session=session,
                timeout_seconds=(
                    request.app.state.settings.session_revocation_check_timeout_seconds
                ),
            ),
        )
        return identity
    except SessionRevocationUnavailableError:
        # M6: store failure is a 503, never a 401 that lies about a live token.
        raise AUTH_UNAVAILABLE.exc() from None
    except InvalidTokenError:
        raise AUTH_TOKEN_INVALID.exc() from None


def require_owner_or_admin(
    identity: Annotated[Identity, Depends(get_identity)],
) -> Identity:
    """Raise 403 if the caller does not hold KEYS_MANAGE (allowlist re-expression).

    Re-expressed over ROLE_PERMISSIONS to use an allowlist instead of a member-denylist.
    Observable behavior is byte-identical for OWNER/ADMIN/MEMBER (the original three roles).
    New tiers (OPERATOR/BILLING_ADMIN/VIEWER) are correctly handled by the matrix:
    OPERATOR holds KEYS_MANAGE → pass; BILLING_ADMIN/VIEWER do not → 403.

    NOTE: Most surfaces should prefer require_permission(<specific_perm>) directly.
    This function is kept for back-compat with the few call sites that have not yet
    been migrated to per-surface permission binding.
    """
    if Permission.KEYS_MANAGE not in ROLE_PERMISSIONS.get(identity.role, frozenset()):
        raise AUTH_FORBIDDEN.exc()
    return identity


def get_create_key_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CreateKeyUseCase:
    return CreateKeyUseCase(SqlAlchemyApiKeyRepository(session), _hasher)


def get_list_keys_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ListKeysUseCase:
    return ListKeysUseCase(SqlAlchemyApiKeyRepository(session))


def get_revoke_key_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RevokeKeyUseCase:
    return RevokeKeyUseCase(SqlAlchemyApiKeyRepository(session))


def get_update_key_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UpdateKeyUseCase:
    return UpdateKeyUseCase(SqlAlchemyApiKeyRepository(session))


def get_rotate_key_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RotateKeyUseCase:
    return RotateKeyUseCase(SqlAlchemyApiKeyRepository(session), _hasher)


def get_authz_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthzUseCase:
    return AuthzUseCase(SqlAlchemyApiKeyRepository(session), _hasher)


def get_authz_authenticator(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CompositeKeyAuthenticator:
    """Build a per-request CompositeKeyAuthenticator for the /internal/authz gate.

    Widens the ext_authz path to accept both sk- API keys (delegated via
    SqlAlchemyKeyAuthenticator/AuthzUseCase, byte-identical) and minted agent tokens
    (resolved via SqlAlchemyAgentOAuthRepository). Mirrors proxy/api/deps.py wiring
    exactly so the edge gate shares one credential-logic source with /v1 in-process.

    Security: fail-closed — any resolution failure raises InvalidApiKeyError → 401.
    Raw token never logged (enforced by CompositeKeyAuthenticator).
    """
    from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository

    _settings = getattr(request.app.state, "settings", None)
    return CompositeKeyAuthenticator(
        api_key_authenticator=SqlAlchemyKeyAuthenticator(
            AuthzUseCase(SqlAlchemyApiKeyRepository(session), _hasher)
        ),
        agent_token_repo=SqlAlchemyAgentOAuthRepository(session),
        hasher=_hasher,
        settings=_settings,
    )
