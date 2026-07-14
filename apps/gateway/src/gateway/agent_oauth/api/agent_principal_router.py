"""FastAPI router for the agent-principal admin API (contract §3, FROZEN @ v1).

Named agent principals: create/list/attach/detach/kill. RBAC ceiling {OWNER, ADMIN}
for create/attach/detach/kill (require_agents_manage); list (M5) is readable by ANY
authenticated tenant role (require_agents_manage is NOT used there).

Kill (M7-M12) is the security-critical surface: atomic, idempotent, tenant-scoped
(byte-identical 404 for unknown/cross-tenant), and audited independently of its own
transaction (fire-and-forget, fail-open — never blocks the 200 response).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from gateway.agent_oauth.api.deps import (
    get_attach_agent_token_use_case,
    get_create_agent_principal_use_case,
    get_detach_agent_token_use_case,
    get_identity,
    get_kill_agent_principal_use_case,
    get_list_agent_principals_use_case,
    require_agents_manage,
)
from gateway.agent_oauth.api.schemas import (
    AgentPrincipalResponse,
    AttachTokenResponse,
    CreateAgentPrincipalRequest,
    DetachTokenResponse,
    KillAgentPrincipalResponse,
    ListAgentPrincipalsResponse,
)
from gateway.agent_oauth.application.principal_use_cases import (
    AttachAgentTokenUseCase,
    CreateAgentPrincipalUseCase,
    DetachAgentTokenUseCase,
    KillAgentPrincipalUseCase,
    ListAgentPrincipalsUseCase,
)
from gateway.agent_oauth.domain.entities import AgentPrincipal
from gateway.agent_oauth.domain.errors import (
    AgentPrincipalKilledError,
    AgentPrincipalNameConflictError,
    AgentPrincipalNotFoundError,
    AgentTokenAlreadyAttachedError,
    AgentTokenNotFoundError,
)
from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.error_catalog import (
    AGENT_PRINCIPAL_KILLED,
    AGENT_PRINCIPAL_NAME_CONFLICT,
    AGENT_PRINCIPAL_NOT_FOUND,
    AGENT_TOKEN_ALREADY_ATTACHED,
    AGENT_TOKEN_NOT_FOUND,
    AUTH_FORBIDDEN,
)
from gateway.keys.domain.errors import ForbiddenError
from gateway.tenants.domain.entities import Identity

agents_router = APIRouter(prefix="/admin/agents", tags=["agent-principals"])


def _to_response(principal: AgentPrincipal) -> AgentPrincipalResponse:
    return AgentPrincipalResponse(
        id=principal.id,
        tenant_id=principal.tenant_id,
        name=principal.name,
        owner_user_id=principal.owner_user_id,
        monthly_budget_usd=(
            str(principal.monthly_budget_usd) if principal.monthly_budget_usd is not None else None
        ),
        rpm_limit=principal.rpm_limit,
        tpm_limit=principal.tpm_limit,
        created_at=principal.created_at,
        last_seen_at=principal.last_seen_at,
        killed_at=principal.killed_at,
        attached_token_count=principal.attached_token_count,
    )


@agents_router.post("", status_code=200, response_model=AgentPrincipalResponse)
async def create_agent_principal(
    body: CreateAgentPrincipalRequest,
    identity: Annotated[Identity, Depends(require_agents_manage)],
    use_case: Annotated[CreateAgentPrincipalUseCase, Depends(get_create_agent_principal_use_case)],
) -> AgentPrincipalResponse:
    """M1: create a named agent principal, tenant-scoped to the caller."""
    try:
        principal = await use_case.execute(
            tenant_id=identity.tenant_id,
            role=identity.role,
            name=body.name,
            owner_user_id=body.owner_user_id,
            monthly_budget_usd=(
                Decimal(body.monthly_budget_usd) if body.monthly_budget_usd is not None else None
            ),
            rpm_limit=body.rpm_limit,
            tpm_limit=body.tpm_limit,
        )
    except ForbiddenError:
        raise AUTH_FORBIDDEN.exc() from None
    except AgentPrincipalNameConflictError:
        raise AGENT_PRINCIPAL_NAME_CONFLICT.exc() from None
    return _to_response(principal)


@agents_router.get("", response_model=ListAgentPrincipalsResponse)
async def list_agent_principals(
    identity: Annotated[Identity, Depends(get_identity)],
    use_case: Annotated[ListAgentPrincipalsUseCase, Depends(get_list_agent_principals_use_case)],
) -> ListAgentPrincipalsResponse:
    """M5: any authenticated tenant role may list."""
    principals = await use_case.execute(tenant_id=identity.tenant_id)
    return ListAgentPrincipalsResponse(agents=[_to_response(p) for p in principals])


@agents_router.post(
    "/{principal_id}/tokens/{token_id}/attach",
    status_code=200,
    response_model=AttachTokenResponse,
)
async def attach_agent_token(
    principal_id: uuid.UUID,
    token_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_agents_manage)],
    use_case: Annotated[AttachAgentTokenUseCase, Depends(get_attach_agent_token_use_case)],
) -> AttachTokenResponse:
    """M2: attach an existing, already-minted agent_tokens row to a principal."""
    try:
        await use_case.execute(
            tenant_id=identity.tenant_id,
            role=identity.role,
            principal_id=principal_id,
            token_id=token_id,
        )
    except ForbiddenError:
        raise AUTH_FORBIDDEN.exc() from None
    except AgentPrincipalNotFoundError:
        raise AGENT_PRINCIPAL_NOT_FOUND.exc() from None
    except AgentTokenNotFoundError:
        raise AGENT_TOKEN_NOT_FOUND.exc() from None
    except AgentPrincipalKilledError:
        raise AGENT_PRINCIPAL_KILLED.exc() from None
    except AgentTokenAlreadyAttachedError:
        raise AGENT_TOKEN_ALREADY_ATTACHED.exc() from None
    return AttachTokenResponse(
        principal_id=principal_id, token_id=token_id, attached_at=datetime.now(UTC)
    )


@agents_router.delete(
    "/{principal_id}/tokens/{token_id}",
    status_code=200,
    response_model=DetachTokenResponse,
)
async def detach_agent_token(
    principal_id: uuid.UUID,
    token_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_agents_manage)],
    use_case: Annotated[DetachAgentTokenUseCase, Depends(get_detach_agent_token_use_case)],
) -> DetachTokenResponse:
    """M3: detach a token — reverts to tenant-wide default governance."""
    try:
        await use_case.execute(
            tenant_id=identity.tenant_id,
            role=identity.role,
            principal_id=principal_id,
            token_id=token_id,
        )
    except ForbiddenError:
        raise AUTH_FORBIDDEN.exc() from None
    except AgentPrincipalNotFoundError:
        raise AGENT_PRINCIPAL_NOT_FOUND.exc() from None
    except AgentTokenNotFoundError:
        raise AGENT_TOKEN_NOT_FOUND.exc() from None
    return DetachTokenResponse(
        principal_id=principal_id, token_id=token_id, detached_at=datetime.now(UTC)
    )


@agents_router.post(
    "/{principal_id}/kill", status_code=200, response_model=KillAgentPrincipalResponse
)
async def kill_agent_principal(
    request: Request,
    principal_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_agents_manage)],
    use_case: Annotated[KillAgentPrincipalUseCase, Depends(get_kill_agent_principal_use_case)],
) -> KillAgentPrincipalResponse:
    """M7-M12: kill a principal — atomic, idempotent, fail-closed, tenant-scoped.

    just_killed distinguishes the ONE call that performed the kill (schedules the
    audit event) from a repeat call observing an already-killed principal (200
    idempotent no-op — same killed_at, no second audit event, no re-revoke — M8).
    """
    try:
        principal, just_killed = await use_case.execute(
            tenant_id=identity.tenant_id,
            role=identity.role,
            principal_id=principal_id,
            now=datetime.now(UTC),
        )
    except ForbiddenError:
        raise AUTH_FORBIDDEN.exc() from None
    except AgentPrincipalNotFoundError:
        raise AGENT_PRINCIPAL_NOT_FOUND.exc() from None

    if just_killed:
        # M11: fire-and-forget audit event, scheduled AFTER the kill's own
        # transaction commits (use_case.execute already awaited it above), never
        # blocking this HTTP response — fail-open (mirrors record_audit's contract).
        assert principal.killed_at is not None
        asyncio.ensure_future(  # noqa: RUF006
            record_audit(
                request.app.state.sessionmaker,
                AuditEvent(
                    id=uuid.uuid4(),
                    tenant_id=identity.tenant_id,
                    actor_user_id=identity.user_id,
                    actor_email=identity.email,
                    action="agent_principal.killed",
                    target_type="agent_principal",
                    target_id=str(principal_id),
                    result="success",
                    metadata={},
                    created_at=datetime.now(UTC),
                ),
            )
        )

    assert principal.killed_at is not None  # kill_principal only returns a killed row
    return KillAgentPrincipalResponse(id=principal.id, killed_at=principal.killed_at)
