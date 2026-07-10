"""JoinTenantByDomainUseCase (domain-capture TASK.md §3 M9/M10 — FROZEN @ v1).

Mirrors tenants/application/use_cases.py::SignupUseCase.execute's check-then-insert shape
(§0 Ground) — weak-password check (zero DB IO) THEN ONE INSERT via
IdentityRepository.join_verified_tenant_domain, which itself catches the `users.email`
UNIQUE IntegrityError and re-raises EmailAlreadyRegisteredError (M9). Deliberately does NOT
call _get_or_provision_sso_user — see domain-capture TASK.md §0 Issues: that helper's
get-or-return-existing branch is a real account-enumeration + false-success bug for a
SIGNUP-shaped operation (an attacker could submit ANY password against a real victim's
already-registered email and receive a misleading 201), not a style preference.

Reuses the EXISTING tenants/domain ports/errors/entities verbatim — zero new error class
for this rejection (M9's own framing).
"""

from __future__ import annotations

import uuid

from gateway.tenants.domain.entities import MIN_PASSWORD_LENGTH
from gateway.tenants.domain.errors import WeakPasswordError
from gateway.tenants.domain.ports import IdentityRepository, PasswordHasher


class JoinTenantByDomainUseCase:
    def __init__(self, repository: IdentityRepository, hasher: PasswordHasher) -> None:
        self._repository = repository
        self._hasher = hasher

    async def execute(self, *, tenant_id: uuid.UUID, email: str, password: str) -> uuid.UUID:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError
        return await self._repository.join_verified_tenant_domain(
            tenant_id=tenant_id,
            email=email.lower(),
            password_hash=self._hasher.hash(password),
        )
