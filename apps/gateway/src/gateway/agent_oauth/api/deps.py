"""Dependency providers for the agent-principal admin API (contract §3, FROZEN @ v1)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.agent_oauth.application.principal_use_cases import (
    AttachAgentTokenUseCase,
    CreateAgentPrincipalUseCase,
    DetachAgentTokenUseCase,
    KillAgentPrincipalUseCase,
    ListAgentPrincipalsUseCase,
    ListPrincipalTokensUseCase,
)
from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
from gateway.core.db import get_session
from gateway.core.error_catalog import AUTH_FORBIDDEN

# Reuse the SAME get_identity FastAPI dependency the keys admin surface uses (JWT
# decode + the impersonation-live-session-guard check) — one shared implementation,
# not a second hand-rolled copy.
from gateway.keys.api.deps import get_identity
from gateway.tenants.domain.authz import ROLE_PERMISSIONS, Permission
from gateway.tenants.domain.entities import Identity


def require_agents_manage(
    identity: Annotated[Identity, Depends(get_identity)],
) -> Identity:
    """Raise 403 if the caller does not hold AGENTS_MANAGE ({OWNER, ADMIN} only).

    agent-identity-governance TASK.md §3 (FROZEN @ v1) — the ONE shared ceiling
    predicate (ROLE_PERMISSIONS), never a second hand-rolled allowlist.
    """
    if Permission.AGENTS_MANAGE not in ROLE_PERMISSIONS.get(identity.role, frozenset()):
        raise AUTH_FORBIDDEN.exc()
    return identity


def get_agent_principal_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SqlAlchemyAgentOAuthRepository:
    return SqlAlchemyAgentOAuthRepository(session)


def get_create_agent_principal_use_case(
    repo: Annotated[SqlAlchemyAgentOAuthRepository, Depends(get_agent_principal_repository)],
) -> CreateAgentPrincipalUseCase:
    return CreateAgentPrincipalUseCase(repo)


def get_list_agent_principals_use_case(
    repo: Annotated[SqlAlchemyAgentOAuthRepository, Depends(get_agent_principal_repository)],
) -> ListAgentPrincipalsUseCase:
    return ListAgentPrincipalsUseCase(repo)


def get_attach_agent_token_use_case(
    repo: Annotated[SqlAlchemyAgentOAuthRepository, Depends(get_agent_principal_repository)],
) -> AttachAgentTokenUseCase:
    return AttachAgentTokenUseCase(repo)


def get_detach_agent_token_use_case(
    repo: Annotated[SqlAlchemyAgentOAuthRepository, Depends(get_agent_principal_repository)],
) -> DetachAgentTokenUseCase:
    return DetachAgentTokenUseCase(repo)


def get_kill_agent_principal_use_case(
    repo: Annotated[SqlAlchemyAgentOAuthRepository, Depends(get_agent_principal_repository)],
) -> KillAgentPrincipalUseCase:
    return KillAgentPrincipalUseCase(repo)


def get_list_principal_tokens_use_case(
    repo: Annotated[SqlAlchemyAgentOAuthRepository, Depends(get_agent_principal_repository)],
) -> ListPrincipalTokensUseCase:
    return ListPrincipalTokensUseCase(repo)
