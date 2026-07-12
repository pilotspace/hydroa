"""Tenant provider-credential value-objects, domain error, and store Protocol.

§3 CONTRACT (provider-credential-store TASK.md) — FROZEN @ v1.

SECURITY INVARIANTS:
- All primary secrets are typed as ``pydantic.SecretStr`` — they are masked in
  repr/str/logs and exposed ONLY via ``.get_secret_value()``.
- ``TenantProviderKeyStore.get`` decrypts in-memory only; it NEVER serializes
  the ORM row to JSON.
- ``ProviderCredentialError`` raised on decrypt failure uses ``from None`` so
  the Fernet ``InvalidToken`` chain (which can carry ciphertext/key material)
  is stripped before reaching crash reporters.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, SecretStr, model_validator

from gateway.proxy.infrastructure.azure_ad import (
    DEFAULT_AUTHORITY,
    DEFAULT_SCOPE,
    AzureADConfig,
)
from gateway.proxy.infrastructure.azure_config import DEFAULT_API_VERSION, AzureConfig
from gateway.proxy.infrastructure.bedrock_sigv4 import AwsCredentials
from gateway.proxy.infrastructure.vertex_ad import VertexServiceAccountConfig

# ---------------------------------------------------------------------------
# Provider name — bounded value-set (TEXT in DB, not a DB ENUM)
# ---------------------------------------------------------------------------

ProviderName = Literal[
    "openrouter", "openai", "anthropic", "google", "bedrock", "azure", "minimax", "vertex"
]

#: Frozenset of the same values for O(1) membership tests at runtime.
PROVIDER_VALUE_SET: frozenset[str] = frozenset(
    {"openrouter", "openai", "anthropic", "google", "bedrock", "azure", "minimax", "vertex"}
)

#: All providers whose per-request auth is resolved from the credential contextvar.
#: Task 3 (dynamic-auth-byok) extends the task-2 Bearer-only set to ALL SIX providers:
#: bedrock and azure now resolve per-request via the contextvar seam, removing the
#: staged env-bound skip. The use-case raises ERR_PROVIDER_KEY_MISSING (402) for any
#: provider in this set when the tenant has no enabled credential.
#: minimax (task minimax-adapter-registry) joins as a plain Bearer BYOK provider.
#: vertex (task vertex-adapter) joins as a GoogleServiceAccountCredential BYOK provider —
#: NOT bearer-auth (see _BEARER_PROVIDERS in provider_keys_admin_router.py, unchanged).
BYOK_PROVIDERS: frozenset[str] = frozenset(
    {"openrouter", "openai", "anthropic", "google", "bedrock", "azure", "minimax", "vertex"}
)


# ---------------------------------------------------------------------------
# Domain error
# ---------------------------------------------------------------------------


class ProviderCredentialError(Exception):
    """Raised by the store for any credential-related failure.

    Carries a stable ``.code`` string that maps to one of the §1 reject codes.
    Task 4 (HTTP router) maps these codes to HTTP status codes.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code: str = code


class ProviderKeyMissing(Exception):
    """Raised when a tenant has no enabled credential for the requested provider.

    §3 CONTRACT (credential-resolution-seam TASK.md) — FROZEN @ v1.

    Carries the provider name ONLY — NEVER a secret, ciphertext, or tenant key.
    The class-level ``.code`` attribute is stable for error catalog mapping.
    HTTP 402 Payment Required: frames BYOK as a provisioning/payment gate.
    """

    code: str = "ERR_PROVIDER_KEY_MISSING"

    def __init__(self, provider: str) -> None:
        super().__init__(f"No enabled credential for provider: {provider}")
        # Instance attribute mirrors the class attribute so callers can inspect
        # either err.code or type(err).code uniformly.
        self.code = "ERR_PROVIDER_KEY_MISSING"


# ---------------------------------------------------------------------------
# BearerCredential — openrouter / openai / anthropic / google
# ---------------------------------------------------------------------------


class BearerCredential(BaseModel):
    """Single-secret bearer token credential for bearer-auth providers.

    ``secret`` is a ``SecretStr``: it is masked in repr/str/logs.
    The ``@model_validator`` rejects empty or whitespace-only secrets at
    construction time with ``ERR_PROVIDER_CREDENTIAL_EMPTY``.
    """

    secret: SecretStr

    @model_validator(mode="after")
    def _validate_non_empty(self) -> BearerCredential:
        if not self.secret.get_secret_value().strip():
            raise ValueError("ERR_PROVIDER_CREDENTIAL_EMPTY")
        return self


# ---------------------------------------------------------------------------
# BedrockCredential — AWS SigV4
# ---------------------------------------------------------------------------


class BedrockCredential(BaseModel):
    """AWS SigV4 credential bundle for Bedrock.

    ``secret_access_key`` and optional ``session_token`` are ``SecretStr``.
    The validator rejects empty ``access_key_id`` or ``region`` with
    ``ERR_PROVIDER_CREDENTIAL_INCOMPLETE``, and an empty
    ``secret_access_key`` with ``ERR_PROVIDER_CREDENTIAL_EMPTY``.
    """

    access_key_id: str
    secret_access_key: SecretStr
    region: str
    session_token: SecretStr | None = None

    @model_validator(mode="after")
    def _validate_fields(self) -> BedrockCredential:
        # Non-secret required fields: empty = incomplete
        if not self.access_key_id.strip():
            raise ValueError("ERR_PROVIDER_CREDENTIAL_INCOMPLETE")
        if not self.region.strip():
            raise ValueError("ERR_PROVIDER_CREDENTIAL_INCOMPLETE")
        # The primary secret must not be empty
        if not self.secret_access_key.get_secret_value().strip():
            raise ValueError("ERR_PROVIDER_CREDENTIAL_EMPTY")
        return self

    def to_aws_credentials(self) -> AwsCredentials:
        """Return the existing frozen ``AwsCredentials`` dataclass.

        Consumed by ``bedrock_sigv4.sign_request``.  The dataclass is the
        contract surface shared with the signer — we return it directly to
        avoid divergent copies.
        """
        token = self.session_token.get_secret_value() if self.session_token is not None else None
        return AwsCredentials(
            access_key_id=self.access_key_id,
            secret_access_key=self.secret_access_key.get_secret_value(),
            region=self.region,
            session_token=token,
        )


# ---------------------------------------------------------------------------
# AzureCredential — api_key OR AAD client-credentials + endpoint/version/map
# ---------------------------------------------------------------------------


class AzureCredential(BaseModel):
    """Azure OpenAI credential: either api-key or AAD client-credentials mode.

    ``api_key`` and ``client_secret`` are ``SecretStr`` so they are masked.
    The validator enforces per-mode completeness:
    - ``mode="api_key"`` → ``api_key`` must be set (non-None, non-empty).
    - ``mode="aad"`` → ``tenant_id``, ``client_id``, and ``client_secret``
      must all be set and non-empty.

    Non-secret fields (``endpoint``, ``api_version``, ``deployment_map``,
    ``scope``, ``authority``) are stored encrypted in ``extra_enc`` by the
    store — they are non-secret but part of the credential envelope.
    """

    mode: Literal["aad", "api_key"]
    endpoint: str
    api_version: str = DEFAULT_API_VERSION
    deployment_map: Mapping[str, str] = {}

    # mode="api_key" fields
    api_key: SecretStr | None = None

    # mode="aad" fields
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None
    scope: str = DEFAULT_SCOPE
    authority: str = DEFAULT_AUTHORITY

    @model_validator(mode="after")
    def _validate_mode_completeness(self) -> AzureCredential:
        if self.mode == "api_key":
            if self.api_key is None or not self.api_key.get_secret_value().strip():
                raise ValueError("ERR_PROVIDER_CREDENTIAL_INCOMPLETE")
        elif self.mode == "aad":
            missing = (
                not self.tenant_id
                or not self.client_id
                or self.client_secret is None
                or not self.client_secret.get_secret_value().strip()
            )
            if missing:
                raise ValueError("ERR_PROVIDER_CREDENTIAL_INCOMPLETE")
        return self

    def to_azure_config(self) -> AzureConfig:
        """Return the existing frozen ``AzureConfig`` dataclass.

        For ``api_key`` mode the key is populated; for ``aad`` mode it is
        an empty string (the AAD token replaces it at request time).
        """
        key = self.api_key.get_secret_value() if self.api_key is not None else ""
        return AzureConfig(
            api_key=key,
            endpoint=self.endpoint,
            api_version=self.api_version,
            deployment_map=dict(self.deployment_map),
        )

    def to_azure_ad_config(self) -> AzureADConfig:
        """Return the existing frozen ``AzureADConfig`` dataclass.

        Only valid when ``mode="aad"``; the validator guarantees the required
        fields are present when this method is reachable.

        ``authority`` is the AAD token-minting host (the client_secret is POSTed
        there) and defaults to ``DEFAULT_AUTHORITY``
        (``https://login.microsoftonline.com``) — it is NEVER derived from the
        resource ``endpoint``. Tests that mint against a local stub set
        ``authority`` to the stub's base URL explicitly.
        """
        return AzureADConfig(
            tenant_id=self.tenant_id or "",
            client_id=self.client_id or "",
            client_secret=self.client_secret.get_secret_value() if self.client_secret else "",
            scope=self.scope,
            authority=self.authority,
        )


# ---------------------------------------------------------------------------
# GoogleServiceAccountCredential — GCP service-account JWT-bearer (Vertex AI)
# ---------------------------------------------------------------------------


class GoogleServiceAccountCredential(BaseModel):
    """GCP service-account credential bundle for Vertex AI (vertex-adapter TASK.md §3).

    Project-scoped, NOT region-scoped (unlike ``BedrockCredential.region``) — ONE
    credential legitimately mints tokens for every seeded Vertex location (§0 Issue #4).

    ``private_key`` is a ``SecretStr`` (PEM-encoded RSA private key) so it is masked
    in repr/str/logs. The validator rejects empty ``project_id``/``client_email`` with
    ``ERR_PROVIDER_CREDENTIAL_INCOMPLETE`` and an empty ``private_key`` with
    ``ERR_PROVIDER_CREDENTIAL_EMPTY`` (mirrors BedrockCredential's split exactly).
    """

    project_id: str
    client_email: str
    private_key: SecretStr
    private_key_id: str | None = None

    @model_validator(mode="after")
    def _validate_fields(self) -> GoogleServiceAccountCredential:
        if not self.project_id.strip():
            raise ValueError("ERR_PROVIDER_CREDENTIAL_INCOMPLETE")
        if not self.client_email.strip():
            raise ValueError("ERR_PROVIDER_CREDENTIAL_INCOMPLETE")
        if not self.private_key.get_secret_value().strip():
            raise ValueError("ERR_PROVIDER_CREDENTIAL_EMPTY")
        return self

    def to_vertex_service_account_config(self) -> VertexServiceAccountConfig:
        """Return the existing frozen ``VertexServiceAccountConfig`` dataclass.

        Consumed by ``VertexTokenProvider`` / ``VertexTokenProviderCache``. Returned
        directly (not copied) to avoid a divergent second shape, same rationale as
        ``BedrockCredential.to_aws_credentials`` / ``AzureCredential.to_azure_ad_config``.
        """
        return VertexServiceAccountConfig(
            project_id=self.project_id,
            client_email=self.client_email,
            private_key=self.private_key.get_secret_value(),
            private_key_id=self.private_key_id,
        )


# ---------------------------------------------------------------------------
# Union type alias
# ---------------------------------------------------------------------------

ProviderCredential = (
    BearerCredential | BedrockCredential | AzureCredential | GoogleServiceAccountCredential
)


# ---------------------------------------------------------------------------
# ProviderKeyStatus — masked list view (NO secret field ever)
# ---------------------------------------------------------------------------


class ProviderKeyStatus(BaseModel):
    """Per-provider status returned by ``TenantProviderKeyStore.list``.

    This model intentionally carries NO secret fields — it is the safe,
    observable view of what credentials are configured and enabled.
    """

    provider: ProviderName
    configured: bool
    enabled: bool
    auth_mode: str | None
    updated_at: datetime


# ---------------------------------------------------------------------------
# TenantProviderKeyStore Protocol port
# ---------------------------------------------------------------------------


@runtime_checkable
class TenantProviderKeyStore(Protocol):
    """Port for per-(tenant, provider) credential persistence.

    Implementations:
    - ``DbTenantProviderKeyStore`` — Fernet-at-rest PostgreSQL store.
    - ``FakeTenantProviderKeyStore`` — zero-DB in-memory fake (tests/app.state).

    All methods are async; the store is the single point of encryption /
    decryption and is NEVER serialized to JSON.
    """

    async def upsert(
        self,
        tenant_id: UUID,
        provider: ProviderName,
        credential: ProviderCredential,
        *,
        enabled: bool = True,
    ) -> None:
        """Persist (or replace) the credential for ``(tenant_id, provider)``.

        Raises ``ProviderCredentialError("ERR_PROVIDER_UNKNOWN")`` when
        ``provider`` is not in the bounded value-set.
        Raises ``ProviderCredentialError("ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE")``
        when the encryption key is not configured.
        """
        ...

    async def get(
        self,
        tenant_id: UUID,
        provider: ProviderName,
    ) -> ProviderCredential | None:
        """Decrypt and return the credential, or ``None`` if absent or disabled.

        Raises ``ProviderCredentialError("ERR_PROVIDER_CREDENTIAL_CORRUPT")``
        (``from None``) when the stored ciphertext cannot be decrypted — the
        ``from None`` strips the Fernet ``InvalidToken`` chain so no
        ciphertext/key material reaches a crash reporter.
        """
        ...

    async def list(
        self,
        tenant_id: UUID,
    ) -> list[ProviderKeyStatus]:
        """Return per-provider status without decrypting any ciphertext.

        The result carries only ``(provider, configured, enabled, auth_mode,
        updated_at)`` — no secret value ever appears here.
        """
        ...

    async def delete(
        self,
        tenant_id: UUID,
        provider: ProviderName,
    ) -> bool:
        """Delete the row and return ``True`` iff a row existed."""
        ...
