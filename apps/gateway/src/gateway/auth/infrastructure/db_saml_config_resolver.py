"""DB-backed SamlConfigResolver adapter.

Queries saml_provider_configs WHERE email_domains @> ARRAY[domain] AND enabled.
Mirrors auth/infrastructure/db_oidc_config_resolver.py — a distinct file per
the §1 Framings decision (new files only, zero edits to OIDC files).

sp_entity_id / acs_url are computed HERE (never persisted — TASK.md §3 Part C
note) from Settings + tenant_id (M2: server-derived, never admin-settable).
"""

from __future__ import annotations

import uuid as _uuid_mod
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from gateway.auth.domain.saml_entities import SamlProviderConfig
    from gateway.core.config import Settings


def derive_sp_entity_id(settings: Settings, tenant_id: _uuid_mod.UUID) -> str:
    """M2: sp_entity_id is SERVER-DERIVED deterministically from tenant_id —
    NEVER accepted from the PUT /admin/saml request body (Ground R6)."""
    base = settings.saml_sp_entity_id_base.rstrip("/")
    return f"{base}/tenant/{tenant_id}"


class DbSamlConfigResolver:
    """Resolves per-tenant SAML config from saml_provider_configs table.

    Injected into the SAML router via app.state.saml_config_resolver.
    Tests inject a fake instead.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def _to_entity(self, row: object) -> SamlProviderConfig:
        from gateway.auth.domain.saml_entities import SamlProviderConfig
        from gateway.auth.infrastructure.saml_orm import SamlProviderConfigRow

        assert isinstance(row, SamlProviderConfigRow)
        return SamlProviderConfig(
            tenant_id=row.tenant_id,
            idp_entity_id=row.idp_entity_id,
            idp_sso_url=row.idp_sso_url,
            idp_x509_cert=row.idp_x509_cert,
            sp_entity_id=derive_sp_entity_id(self._settings, row.tenant_id),
            acs_url=self._settings.saml_acs_url,
            email_domains=list(row.email_domains or []),
            email_attribute_name=row.email_attribute_name,
            enabled=row.enabled,
        )

    async def resolve(self, domain: str | None) -> SamlProviderConfig | None:
        """Return per-tenant SamlProviderConfig for the given email domain.

        Returns None when domain is None or no enabled row matches (M1 →
        ERR_SAML_NOT_CONFIGURED).
        """
        from gateway.auth.infrastructure.saml_orm import SamlProviderConfigRow

        if domain is None:
            return None

        stmt = select(SamlProviderConfigRow).where(
            SamlProviderConfigRow.email_domains.contains([domain]),
            SamlProviderConfigRow.enabled.is_(True),
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return self._to_entity(row)

    async def resolve_by_tenant_id(self, tenant_id: str) -> SamlProviderConfig | None:
        """Resolve config by tenant_id string — used at /acs after the M3
        pending-request lookup resolves the pinned tenant."""
        from gateway.auth.infrastructure.saml_orm import SamlProviderConfigRow

        try:
            tid = _uuid_mod.UUID(tenant_id)
        except (ValueError, AttributeError, TypeError):
            return None

        stmt = select(SamlProviderConfigRow).where(
            SamlProviderConfigRow.tenant_id == tid,
            SamlProviderConfigRow.enabled.is_(True),
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return self._to_entity(row)
