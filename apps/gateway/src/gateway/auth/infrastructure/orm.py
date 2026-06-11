"""ORM model for oidc_provider_configs table.

SECURITY INVARIANTS:
- client_secret_enc is BYTEA (Fernet ciphertext) — NEVER the plaintext.
- The plaintext is decrypted ONLY in DbOidcConfigResolver.resolve() in memory.
- This ORM model is NEVER serialized to JSON responses.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class OidcProviderConfigRow(Base):
    """SQLAlchemy ORM model for oidc_provider_configs.

    Mirrors the DDL in migration a9b3c4d5e6f7_oidc_tenant_config.py.
    """

    __tablename__ = "oidc_provider_configs"
    __table_args__ = (
        # GIN index for email_domains @> containment queries —
        # matches the index created by migration a9b3c4d5e6f7.
        Index(
            "ix_oidc_provider_configs_email_domains",
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
    issuer: Mapped[str] = mapped_column(sa.VARCHAR, nullable=False)
    client_id: Mapped[str] = mapped_column(sa.VARCHAR, nullable=False)
    # BYTEA — Fernet-encrypted ciphertext; NEVER the plaintext client_secret
    client_secret_enc: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    # empty string = derive from issuer + "/authorize"
    authorize_url: Mapped[str] = mapped_column(
        sa.VARCHAR, nullable=False, server_default=text("''")
    )
    token_url: Mapped[str] = mapped_column(sa.VARCHAR, nullable=False)
    jwks_url: Mapped[str] = mapped_column(sa.VARCHAR, nullable=False)
    email_domains: Mapped[list[str]] = mapped_column(
        ARRAY(sa.TEXT), nullable=False, server_default=text("'{}'")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.true())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
