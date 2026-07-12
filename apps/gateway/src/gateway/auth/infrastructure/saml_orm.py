"""ORM model for saml_provider_configs table.

Mirrors auth/infrastructure/orm.py::OidcProviderConfigRow — a distinct file per
the §1 Framings decision (new files only, zero edits to OIDC files).

SECURITY NOTE: idp_x509_cert is TEXT (PEM, public key material) — NOT a secret,
no Fernet encryption (TASK.md §3 Part C note, a deliberate divergence from
OIDC's client_secret_enc handling — M10).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base

_DEFAULT_EMAIL_ATTRIBUTE_NAME = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"


class SamlProviderConfigRow(Base):
    """SQLAlchemy ORM model for saml_provider_configs.

    Mirrors the DDL in migration <rev>_saml_tenant_config.py.
    sp_entity_id / acs_url are NOT columns — both are server-derived at read
    time (TASK.md §3 Part C note).
    """

    __tablename__ = "saml_provider_configs"
    __table_args__ = (
        Index(
            "ix_saml_provider_configs_email_domains",
            "email_domains",
            postgresql_using="gin",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    idp_entity_id: Mapped[str] = mapped_column(sa.VARCHAR, nullable=False)
    idp_sso_url: Mapped[str] = mapped_column(sa.VARCHAR, nullable=False)
    idp_x509_cert: Mapped[str] = mapped_column(sa.TEXT, nullable=False)
    email_domains: Mapped[list[str]] = mapped_column(
        ARRAY(sa.TEXT), nullable=False, server_default=text("'{}'")
    )
    email_attribute_name: Mapped[str] = mapped_column(
        sa.VARCHAR,
        nullable=False,
        server_default=text(f"'{_DEFAULT_EMAIL_ATTRIBUTE_NAME}'"),
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
