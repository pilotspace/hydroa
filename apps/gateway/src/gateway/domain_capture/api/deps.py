"""Dependency wiring for the domain-claims API (domain-capture TASK.md §3 — FROZEN @ v1).

Mirrors auth/api/saml_deps.py's own shape exactly: app.state.<name> is the test-injection
seam (tests inject fakes; production constructs the real adapter here on each call).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from gateway.domain_capture.application.create_claim_use_case import (
        CreateDomainClaimUseCase,
    )
    from gateway.domain_capture.application.join_tenant_use_case import (
        JoinTenantByDomainUseCase,
    )
    from gateway.domain_capture.application.revoke_claim_use_case import (
        RevokeDomainClaimUseCase,
    )
    from gateway.domain_capture.application.registrar_hint_use_case import (
        GetRegistrarHintUseCase,
    )
    from gateway.domain_capture.application.verify_claim_use_case import (
        VerifyDomainClaimUseCase,
    )
    from gateway.domain_capture.domain.ports import (
        DnsNsResolver,
        DnsTxtResolver,
        DomainClaimRepository,
        DomainClaimResolver,
    )
    from gateway.domain_capture.infrastructure.rate_limiter import DomainClaimRateLimiter


def get_domain_claim_repository(request: Request, session: AsyncSession) -> DomainClaimRepository:
    injected: DomainClaimRepository | None = getattr(
        request.app.state, "domain_claim_repository", None
    )
    if injected is not None:
        return injected

    from gateway.domain_capture.infrastructure.repository import (
        SqlAlchemyDomainClaimRepository,
    )

    return SqlAlchemyDomainClaimRepository(session)


def get_domain_claim_resolver(request: Request, session: AsyncSession) -> DomainClaimResolver:
    """Resolve DomainClaimResolver — test seam via app.state.domain_claim_resolver.

    Production shares the SAME repository instance as get_domain_claim_repository would
    build (SqlAlchemyDomainClaimRepository implements both Protocols) — a fresh instance
    per call is cheap (holds only the session reference, no IO at construction).
    """
    injected: DomainClaimResolver | None = getattr(request.app.state, "domain_claim_resolver", None)
    if injected is not None:
        return injected

    from gateway.domain_capture.infrastructure.repository import (
        SqlAlchemyDomainClaimRepository,
    )

    return SqlAlchemyDomainClaimRepository(session)


def get_dns_resolver(request: Request) -> DnsTxtResolver:
    injected: DnsTxtResolver | None = getattr(request.app.state, "dns_resolver", None)
    if injected is not None:
        return injected

    from gateway.domain_capture.infrastructure.dns_resolver import DnsPythonTxtResolver

    return DnsPythonTxtResolver()


def get_domain_claim_rate_limiter(request: Request) -> DomainClaimRateLimiter:
    injected: DomainClaimRateLimiter | None = getattr(
        request.app.state, "domain_claim_rate_limiter", None
    )
    if injected is not None:
        return injected

    from gateway.domain_capture.infrastructure.rate_limiter import DomainClaimRateLimiter

    return DomainClaimRateLimiter(redis=request.app.state.redis_client)


def get_create_claim_use_case(request: Request, session: AsyncSession) -> CreateDomainClaimUseCase:
    from gateway.domain_capture.application.create_claim_use_case import (
        CreateDomainClaimUseCase,
    )

    return CreateDomainClaimUseCase(get_domain_claim_repository(request, session))


def get_verify_claim_use_case(request: Request, session: AsyncSession) -> VerifyDomainClaimUseCase:
    from gateway.domain_capture.application.verify_claim_use_case import (
        VerifyDomainClaimUseCase,
    )

    settings = request.app.state.settings
    return VerifyDomainClaimUseCase(
        get_domain_claim_repository(request, session),
        get_dns_resolver(request),
        dns_timeout_seconds=settings.domain_verification_dns_timeout_seconds,
    )


def get_revoke_claim_use_case(request: Request, session: AsyncSession) -> RevokeDomainClaimUseCase:
    from gateway.domain_capture.application.revoke_claim_use_case import (
        RevokeDomainClaimUseCase,
    )

    return RevokeDomainClaimUseCase(get_domain_claim_repository(request, session))


def get_dns_ns_resolver(request: Request) -> DnsNsResolver:
    injected: DnsNsResolver | None = getattr(request.app.state, "dns_ns_resolver", None)
    if injected is not None:
        return injected

    from gateway.domain_capture.infrastructure.dns_resolver import DnsPythonNsResolver

    return DnsPythonNsResolver()


def get_registrar_hint_use_case(request: Request) -> GetRegistrarHintUseCase:
    from gateway.domain_capture.application.registrar_hint_use_case import (
        GetRegistrarHintUseCase,
    )

    settings = request.app.state.settings
    return GetRegistrarHintUseCase(
        get_dns_ns_resolver(request),
        dns_timeout_seconds=settings.registrar_hint_dns_timeout_seconds,
    )


def get_join_tenant_use_case(request: Request, session: AsyncSession) -> JoinTenantByDomainUseCase:
    """Reuses the SAME SqlAlchemyIdentityRepository + PasswordHasher construction the
    existing tenants/api/deps.py::get_signup_use_case uses — no new port adapter, just a
    different use-case class wrapping them (M9)."""
    from gateway.domain_capture.application.join_tenant_use_case import (
        JoinTenantByDomainUseCase,
    )
    from gateway.tenants.infrastructure.repository import SqlAlchemyIdentityRepository

    hasher = request.app.state.password_hasher
    return JoinTenantByDomainUseCase(SqlAlchemyIdentityRepository(session), hasher)
