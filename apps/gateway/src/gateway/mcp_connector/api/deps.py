"""Dependency providers for the MCP connector API (mcp-connector-passthrough TASK.md §3).

M14: auth for /v1/mcp/call reuses `CompositeKeyAuthenticator` UNCHANGED — this module
constructs it exactly as `proxy/api/deps.py:get_completion_use_case` does (same
per-request session-scoped wiring), never modifying that class's signature or behavior.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
from gateway.core.db import get_session
from gateway.core.egress_policy import DenyPrivateAndMetadataEgressPolicy, EgressPolicy
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.mcp_connector.application.use_cases import McpCallUseCase
from gateway.mcp_connector.domain.ports import NoopToolCallObserver, ToolCallObserver
from gateway.mcp_connector.infrastructure.breaker_registry import McpCircuitBreakerRegistry
from gateway.mcp_connector.infrastructure.httpx_dialer import HttpxMcpDialer
from gateway.mcp_connector.infrastructure.repository import McpServerPolicyRepository
from gateway.proxy.application.governance import NonChatGovernance
from gateway.proxy.infrastructure.composite_key_authenticator import CompositeKeyAuthenticator
from gateway.proxy.infrastructure.guardrail_evaluator import RegexGuardrailEvaluator
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator
from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker

_hasher = Sha256SecretHasher()
_repo = McpServerPolicyRepository()


def get_mcp_authenticator(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CompositeKeyAuthenticator:
    """Build a per-request CompositeKeyAuthenticator — UNCHANGED (M14), mirrors
    proxy/api/deps.py:get_completion_use_case's own construction exactly."""
    repo = SqlAlchemyApiKeyRepository(session)
    authz_use_case = AuthzUseCase(repo, _hasher)
    settings = getattr(request.app.state, "settings", None)
    return CompositeKeyAuthenticator(
        api_key_authenticator=SqlAlchemyKeyAuthenticator(authz_use_case),
        agent_token_repo=SqlAlchemyAgentOAuthRepository(session),
        hasher=_hasher,
        settings=settings,
    )


def get_mcp_egress_policy(request: Request) -> EgressPolicy:
    """Tests override via app.state.egress_policy (e.g. an injectable-resolver
    DenyPrivateAndMetadataEgressPolicy, or AllowAllEgressPolicy for non-security cases).
    Production default: the real, deny-by-default policy — never AllowAllEgressPolicy."""
    policy = getattr(request.app.state, "mcp_egress_policy", None)
    if policy is not None:
        return policy
    return DenyPrivateAndMetadataEgressPolicy()


def get_mcp_call_use_case(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> McpCallUseCase:
    settings = getattr(request.app.state, "settings", None)
    dial_timeout = float(getattr(settings, "mcp_connector_dial_timeout_seconds", 30.0))

    dialer = getattr(request.app.state, "mcp_dialer", None)
    if dialer is None:
        dialer = HttpxMcpDialer(dial_timeout_seconds=dial_timeout)

    breaker_registry = getattr(request.app.state, "mcp_breaker_registry", None)
    if breaker_registry is None:
        breaker_registry = McpCircuitBreakerRegistry(
            failure_threshold=int(getattr(settings, "mcp_connector_breaker_failure_threshold", 5)),
            cooldown_seconds=float(
                getattr(settings, "mcp_connector_breaker_cooldown_seconds", 30.0)
            ),
        )
        request.app.state.mcp_breaker_registry = breaker_registry

    tool_call_observer: ToolCallObserver = (
        getattr(request.app.state, "mcp_tool_call_observer", None) or NoopToolCallObserver()
    )

    guardrail_evaluator = getattr(request.app.state, "guardrail_evaluator", None)
    if guardrail_evaluator is None:
        guardrail_evaluator = RegexGuardrailEvaluator()

    payload_capture = getattr(request.app.state, "payload_capture", None)

    # mcp-budget-governance CR (mcp-connector-passthrough TASK.md §3 -> v3):
    # NonChatGovernance, wired the SAME way embeddings_deps.py/images_deps.py/
    # audio_deps.py already do (budget_guard/redis_client/session_factory from
    # app.state) — but only its check_budgets() subset is ever called by
    # McpCallUseCase, so authenticator/model_checker/rate_limiter below are never
    # invoked; they exist only to satisfy NonChatGovernance's constructor.
    # authenticator reuses get_mcp_authenticator's own construction verbatim (M14
    # — zero behavior change to the real request-authenticating instance, which
    # the router builds separately via the get_mcp_authenticator dependency).
    governance = NonChatGovernance(
        authenticator=get_mcp_authenticator(request, session),
        model_checker=SqlAlchemyModelChecker(session),
        budget_guard=request.app.state.budget_guard,
        rate_limiter=None,
        redis_client=getattr(request.app.state, "redis_client", None),
        session_factory=getattr(request.app.state, "sessionmaker", None),
    )

    return McpCallUseCase(
        session=session,
        audit_session_factory=request.app.state.sessionmaker,
        repo=_repo,
        dialer=dialer,
        egress_policy=get_mcp_egress_policy(request),
        guardrail_evaluator=guardrail_evaluator,
        payload_capture=payload_capture,
        tool_call_observer=tool_call_observer,
        breaker_registry=breaker_registry,
        governance=governance,
    )


__all__ = [
    "get_mcp_authenticator",
    "get_mcp_call_use_case",
    "get_mcp_egress_policy",
]
