"""ORM model for tenant_provider_keys table.

SECURITY INVARIANTS:
- secret_enc and extra_enc are BYTEA (Fernet ciphertext) — NEVER the plaintext.
- Plaintext is decrypted ONLY in DbTenantProviderKeyStore.get() in memory.
- This ORM model is NEVER serialized to JSON responses.
- auth_mode is the only plaintext discriminator stored in the row (non-secret).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, ForeignKey, func
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class TenantProviderKeyRow(Base):
    """SQLAlchemy ORM model for tenant_provider_keys.

    Mirrors the DDL in migration <newhex>_tenant_provider_keys.py.
    PK is composite (tenant_id, provider) — one row per provider per tenant.

    SECURITY INVARIANTS (per-row):
    - secret_enc: BYTEA Fernet ciphertext of the primary secret.
      Bearer → the bearer token string.
      Bedrock → secret_access_key.
      Azure api_key → the api_key string.
      Azure aad → the client_secret string.
    - extra_enc: BYTEA Fernet ciphertext of a JSON object carrying remaining
      credential fields (NULL for Bearer; non-NULL for Bedrock and Azure).
      Bedrock extra JSON: {access_key_id, region, session_token}.
      Azure extra JSON: {endpoint, api_version, deployment_map, tenant_id,
                         client_id, scope, authority}.
    - auth_mode: plaintext TEXT discriminator ("aad" | "api_key") for Azure;
      NULL for all other providers. Non-secret; used by list() without decrypt.
    - enabled: when False, get() returns None (unconfigured-for-resolution).
    - The plaintext is recoverable ONLY via get() in-memory; list() never decrypts.
    """

    __tablename__ = "tenant_provider_keys"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(
        sa.TEXT,
        primary_key=True,
        nullable=False,
    )
    # BYTEA — Fernet-encrypted ciphertext of the PRIMARY secret; NEVER the plaintext
    secret_enc: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    # BYTEA — Fernet-encrypted ciphertext of a JSON object of remaining fields; NULL for Bearer
    extra_enc: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    # Plaintext discriminator for Azure dual-mode: "aad" | "api_key"; NULL for other providers
    auth_mode: Mapped[str | None] = mapped_column(sa.TEXT, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.true())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
