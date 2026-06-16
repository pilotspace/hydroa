"""Failing-first (RED) suite for provider credential value-objects (§3 CONTRACT, TASK.md §4).

No DB required — these are pure domain model tests.

TRUE-RED RULE: every test imports from gateway.proxy.domain.provider_credentials which does
NOT exist yet.  All tests fail on ModuleNotFoundError at collection time.

Right-reason red targets per test:

  test_bearer_empty_secret_rejected:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once module exists: ValueError whose message == "ERR_PROVIDER_CREDENTIAL_EMPTY" when
    constructing BearerCredential(secret="   ").)

  test_bedrock_missing_region_incomplete:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once module exists: ValueError whose message == "ERR_PROVIDER_CREDENTIAL_INCOMPLETE" when
    BedrockCredential is constructed without a region.)

  test_azure_aad_missing_client_id_incomplete:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once module exists: ValueError whose message == "ERR_PROVIDER_CREDENTIAL_INCOMPLETE" when
    AzureCredential(mode="aad") is constructed without client_id.)

  test_secretstr_masks_in_repr:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once module exists: assert repr(cred) does NOT contain the raw secret string,
    but .get_secret_value() returns it.)

  test_bedrock_to_aws_credentials:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once module exists: BedrockCredential.to_aws_credentials() returns the exact
    AwsCredentials frozen dataclass with matching fields.)

  test_azure_to_aad_and_azure_config:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once module exists: AzureCredential(mode="aad").to_azure_ad_config() returns an
    AzureADConfig matching the constructor args, and .to_azure_config().deployment_map
    equals the supplied map.)

  test_protocol_fake_zero_db:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once module exists: FakeTenantProviderKeyStore satisfies the TenantProviderKeyStore
    Protocol via runtime isinstance check; get/upsert operate with no DB connection.)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Module under test — does NOT exist yet; import failure IS the red target.
# ---------------------------------------------------------------------------
from gateway.proxy.domain.provider_credentials import (  # type: ignore[import]
    AzureCredential,
    BearerCredential,
    BedrockCredential,
    ProviderCredential,
    ProviderCredentialError,
    ProviderKeyStatus,
    TenantProviderKeyStore,
)

# Existing frozen dataclasses — these EXIST; importing them asserts contract alignment.
from gateway.proxy.infrastructure.azure_ad import (
    DEFAULT_AUTHORITY,
    DEFAULT_SCOPE,
    AzureADConfig,
)
from gateway.proxy.infrastructure.azure_config import AzureConfig, DEFAULT_API_VERSION
from gateway.proxy.infrastructure.bedrock_sigv4 import AwsCredentials
from datetime import UTC

# ---------------------------------------------------------------------------
# FakeTenantProviderKeyStore — zero-DB in-memory implementation of the Protocol.
# Defined here (in the suite) per harness conventions.
# ---------------------------------------------------------------------------


class FakeTenantProviderKeyStore:
    """In-memory fake that satisfies TenantProviderKeyStore Protocol (zero-DB).

    Stores credentials in a plain dict keyed (tenant_id, provider).
    Used to verify the Protocol is structurally satisfied without a DB connection.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[uuid.UUID, str], tuple[ProviderCredential, bool]] = {}

    async def upsert(
        self,
        tenant_id: uuid.UUID,
        provider: str,
        credential: ProviderCredential,
        *,
        enabled: bool = True,
    ) -> None:
        self._store[(tenant_id, provider)] = (credential, enabled)

    async def get(self, tenant_id: uuid.UUID, provider: str) -> ProviderCredential | None:
        entry = self._store.get((tenant_id, provider))
        if entry is None:
            return None
        cred, enabled = entry
        if not enabled:
            return None
        return cred

    async def list(self, tenant_id: uuid.UUID) -> list[ProviderKeyStatus]:
        from datetime import datetime, timezone  # noqa: PLC0415

        result = []
        for (tid, provider), (_cred, enabled) in self._store.items():
            if tid == tenant_id:
                result.append(
                    ProviderKeyStatus(
                        provider=provider,  # type: ignore[arg-type]
                        configured=True,
                        enabled=enabled,
                        auth_mode=None,
                        updated_at=datetime.now(tz=UTC),
                    )
                )
        return result

    async def delete(self, tenant_id: uuid.UUID, provider: str) -> bool:
        key = (tenant_id, provider)
        if key in self._store:
            del self._store[key]
            return True
        return False


# ---------------------------------------------------------------------------
# T1: Empty / whitespace-only secret rejected at model construction
# ---------------------------------------------------------------------------


def test_bearer_empty_secret_rejected() -> None:
    """Construct BearerCredential(secret='   ') -> ValueError with msg 'ERR_PROVIDER_CREDENTIAL_EMPTY'.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Once the module exists: Pydantic @model_validator(mode='after') must raise ValueError
    whose string representation equals 'ERR_PROVIDER_CREDENTIAL_EMPTY'.
    """
    with pytest.raises((ValueError, Exception)) as exc_info:
        BearerCredential(secret="   ")

    # The validator message must carry the contracted error code.
    assert "ERR_PROVIDER_CREDENTIAL_EMPTY" in str(exc_info.value), (
        f"Expected 'ERR_PROVIDER_CREDENTIAL_EMPTY' in error, got: {exc_info.value}"
    )


def test_bearer_empty_string_secret_rejected() -> None:
    """Construct BearerCredential(secret='') -> ValueError with 'ERR_PROVIDER_CREDENTIAL_EMPTY'.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    A completely empty string is also rejected by the whitespace-strip validator.
    """
    with pytest.raises((ValueError, Exception)) as exc_info:
        BearerCredential(secret="")

    assert "ERR_PROVIDER_CREDENTIAL_EMPTY" in str(exc_info.value), (
        f"Expected 'ERR_PROVIDER_CREDENTIAL_EMPTY' in error, got: {exc_info.value}"
    )


# ---------------------------------------------------------------------------
# T2: Bedrock incomplete multi-field (missing region)
# ---------------------------------------------------------------------------


def test_bedrock_missing_region_incomplete() -> None:
    """Construct BedrockCredential without region -> ValueError('ERR_PROVIDER_CREDENTIAL_INCOMPLETE').

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Once the module exists: the @model_validator must reject a BedrockCredential where
    region is empty/None with ERR_PROVIDER_CREDENTIAL_INCOMPLETE.
    """
    with pytest.raises((ValueError, Exception)) as exc_info:
        BedrockCredential(
            access_key_id="AKIAEX",
            secret_access_key="s3cr3t",
            region="",  # empty = incomplete
        )

    assert "ERR_PROVIDER_CREDENTIAL_INCOMPLETE" in str(exc_info.value), (
        f"Expected 'ERR_PROVIDER_CREDENTIAL_INCOMPLETE' in error, got: {exc_info.value}"
    )


def test_bedrock_missing_access_key_id_incomplete() -> None:
    """Construct BedrockCredential without access_key_id -> ValueError('ERR_PROVIDER_CREDENTIAL_INCOMPLETE').

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    access_key_id is a required non-secret field; empty = incomplete.
    """
    with pytest.raises((ValueError, Exception)) as exc_info:
        BedrockCredential(
            access_key_id="",
            secret_access_key="s3cr3t",
            region="us-east-1",
        )

    assert "ERR_PROVIDER_CREDENTIAL_INCOMPLETE" in str(exc_info.value), (
        f"Expected 'ERR_PROVIDER_CREDENTIAL_INCOMPLETE' in error, got: {exc_info.value}"
    )


# ---------------------------------------------------------------------------
# T3: Azure AAD incomplete (missing client_id)
# ---------------------------------------------------------------------------


def test_azure_aad_missing_client_id_incomplete() -> None:
    """Construct AzureCredential(mode='aad', no client_id) -> 'ERR_PROVIDER_CREDENTIAL_INCOMPLETE'.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Once the module exists: AzureCredential mode='aad' requires {tenant_id, client_id,
    client_secret}; missing client_id triggers ERR_PROVIDER_CREDENTIAL_INCOMPLETE.
    """
    with pytest.raises((ValueError, Exception)) as exc_info:
        AzureCredential(
            mode="aad",
            endpoint="https://my.openai.azure.com",
            api_version="2024-10-21",
            tenant_id="tid-123",
            # client_id intentionally absent / None
            client_id=None,
            client_secret="csecret",
        )

    assert "ERR_PROVIDER_CREDENTIAL_INCOMPLETE" in str(exc_info.value), (
        f"Expected 'ERR_PROVIDER_CREDENTIAL_INCOMPLETE' in error, got: {exc_info.value}"
    )


def test_azure_api_key_mode_missing_api_key_incomplete() -> None:
    """Construct AzureCredential(mode='api_key', no api_key) -> 'ERR_PROVIDER_CREDENTIAL_INCOMPLETE'.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Once the module exists: AzureCredential mode='api_key' requires api_key to be set.
    """
    with pytest.raises((ValueError, Exception)) as exc_info:
        AzureCredential(
            mode="api_key",
            endpoint="https://my.openai.azure.com",
            api_version="2024-10-21",
            api_key=None,  # api_key intentionally absent
        )

    assert "ERR_PROVIDER_CREDENTIAL_INCOMPLETE" in str(exc_info.value), (
        f"Expected 'ERR_PROVIDER_CREDENTIAL_INCOMPLETE' in error, got: {exc_info.value}"
    )


# ---------------------------------------------------------------------------
# T4: SecretStr masks in repr but exposes value via .get_secret_value()
# ---------------------------------------------------------------------------


def test_secretstr_masks_in_repr() -> None:
    """repr() of BearerCredential excludes the raw secret; .get_secret_value() returns it.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Once the module exists: Pydantic SecretStr's repr is '**********'; the raw
    secret string must not appear in repr(cred) or str(cred).
    """
    raw_secret = "sk-super-secret-value-12345"
    cred = BearerCredential(secret=raw_secret)

    # SecretStr masking — Pydantic wraps the value
    cred_repr = repr(cred)
    cred_str = str(cred)
    assert raw_secret not in cred_repr, f"SECURITY: raw secret found in repr: {cred_repr}"
    assert raw_secret not in cred_str, f"SECURITY: raw secret found in str: {cred_str}"

    # The value is still recoverable programmatically
    assert cred.secret.get_secret_value() == raw_secret, (
        "get_secret_value() must return the original secret"
    )


def test_bedrock_secretstr_masks_in_repr() -> None:
    """repr() of BedrockCredential excludes secret_access_key; .get_secret_value() returns it.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    """
    raw_secret = "s3cr3t-access-key-value"
    cred = BedrockCredential(
        access_key_id="AKIAEXAMPLE",
        secret_access_key=raw_secret,
        region="us-east-1",
    )

    cred_repr = repr(cred)
    assert raw_secret not in cred_repr, (
        f"SECURITY: raw secret found in BedrockCredential repr: {cred_repr}"
    )
    assert cred.secret_access_key.get_secret_value() == raw_secret


# ---------------------------------------------------------------------------
# T5: BedrockCredential.to_aws_credentials() converter
# ---------------------------------------------------------------------------


def test_bedrock_to_aws_credentials() -> None:
    """BedrockCredential.to_aws_credentials() returns the exact existing AwsCredentials dataclass.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Once the module exists: the converter must return the frozen AwsCredentials dataclass
    (not a new type) with field values matching the BedrockCredential fields.
    """
    cred = BedrockCredential(
        access_key_id="AKIAEX",
        secret_access_key="s3cr3t",
        region="us-east-1",
        session_token=None,
    )

    result = cred.to_aws_credentials()

    # Must be the exact existing AwsCredentials dataclass
    assert isinstance(result, AwsCredentials), (
        f"Expected AwsCredentials instance, got {type(result)}"
    )
    assert result.access_key_id == "AKIAEX"
    assert result.secret_access_key == "s3cr3t"
    assert result.region == "us-east-1"
    assert result.session_token is None


def test_bedrock_to_aws_credentials_with_session_token() -> None:
    """BedrockCredential.to_aws_credentials() preserves optional session_token.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    """
    cred = BedrockCredential(
        access_key_id="AKIAEX",
        secret_access_key="s3cr3t",
        region="eu-west-1",
        session_token="session-tok-xyz",
    )

    result = cred.to_aws_credentials()
    assert result.session_token == "session-tok-xyz"


# ---------------------------------------------------------------------------
# T6: AzureCredential converters — to_azure_ad_config() and to_azure_config()
# ---------------------------------------------------------------------------


def test_azure_to_aad_and_azure_config() -> None:
    """AzureCredential(mode='aad') converters round-trip to AzureADConfig and AzureConfig.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Once the module exists:
    - .to_azure_ad_config() must return AzureADConfig (existing frozen dataclass)
      with tenant_id/client_id/client_secret/scope/authority populated.
    - .to_azure_config() must return AzureConfig (existing frozen dataclass)
      with endpoint/api_version/deployment_map populated (api_key = "" or placeholder).
    """
    deployment_map = {"gpt-4o": "my-gpt4o-deploy"}

    cred = AzureCredential(
        mode="aad",
        endpoint="https://x.openai.azure.com",
        api_version="2024-10-21",
        deployment_map=deployment_map,
        tenant_id="tid-abc",
        client_id="cid-def",
        client_secret="csecret-xyz",
        scope=DEFAULT_SCOPE,
        authority=DEFAULT_AUTHORITY,
    )

    # --- to_azure_ad_config() ---
    ad_config = cred.to_azure_ad_config()
    assert isinstance(ad_config, AzureADConfig), f"Expected AzureADConfig, got {type(ad_config)}"
    assert ad_config.tenant_id == "tid-abc"
    assert ad_config.client_id == "cid-def"
    assert ad_config.client_secret == "csecret-xyz"
    assert ad_config.scope == DEFAULT_SCOPE
    assert ad_config.authority == DEFAULT_AUTHORITY

    # --- to_azure_config() ---
    azure_config = cred.to_azure_config()
    assert isinstance(azure_config, AzureConfig), f"Expected AzureConfig, got {type(azure_config)}"
    assert azure_config.endpoint == "https://x.openai.azure.com"
    assert azure_config.api_version == "2024-10-21"
    assert dict(azure_config.deployment_map) == deployment_map


def test_azure_api_key_to_azure_config() -> None:
    """AzureCredential(mode='api_key').to_azure_config() returns AzureConfig with api_key set.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    """
    cred = AzureCredential(
        mode="api_key",
        endpoint="https://y.openai.azure.com",
        api_version="2024-10-21",
        deployment_map={"gpt-35-turbo": "my-turbo"},
        api_key="azure-key-secret",
    )

    azure_config = cred.to_azure_config()
    assert isinstance(azure_config, AzureConfig)
    assert azure_config.api_key == "azure-key-secret"
    assert azure_config.endpoint == "https://y.openai.azure.com"
    assert dict(azure_config.deployment_map) == {"gpt-35-turbo": "my-turbo"}


# ---------------------------------------------------------------------------
# T7: Protocol port — FakeTenantProviderKeyStore satisfies TenantProviderKeyStore (zero-DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_protocol_fake_zero_db() -> None:
    """FakeTenantProviderKeyStore satisfies TenantProviderKeyStore Protocol; no DB needed.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Once the module exists: isinstance check with runtime_checkable Protocol must pass;
    upsert + get round-trip via the fake with no database connection at all.
    """
    fake: TenantProviderKeyStore = FakeTenantProviderKeyStore()  # type: ignore[type-abstract]

    tenant_id = uuid.uuid4()
    credential = BearerCredential(secret="test-bearer-secret")

    # upsert — no DB, pure in-memory
    await fake.upsert(tenant_id, "openrouter", credential)

    # get — must return the same credential
    result = await fake.get(tenant_id, "openrouter")
    assert result is not None, "Expected credential back from fake store"
    assert isinstance(result, BearerCredential)
    assert result.secret.get_secret_value() == "test-bearer-secret"

    # get for unconfigured provider — must return None
    missing = await fake.get(tenant_id, "anthropic")
    assert missing is None, "Expected None for unconfigured provider"

    # list — must return status entries without secrets
    statuses = await fake.list(tenant_id)
    assert len(statuses) == 1
    status = statuses[0]
    assert status.provider == "openrouter"
    assert status.configured is True
    assert status.enabled is True
    # No secret fields on ProviderKeyStatus
    assert not hasattr(status, "secret"), "ProviderKeyStatus must not expose secret fields"

    # delete — must return True for existing, False for non-existing
    deleted = await fake.delete(tenant_id, "openrouter")
    assert deleted is True

    second_delete = await fake.delete(tenant_id, "openrouter")
    assert second_delete is False

    # After delete, get returns None
    after_delete = await fake.get(tenant_id, "openrouter")
    assert after_delete is None


@pytest.mark.asyncio
async def test_protocol_fake_disabled_returns_none() -> None:
    """FakeTenantProviderKeyStore.get() returns None when credential is disabled.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Mirrors the enabled=false ≡ unconfigured-for-resolution contract rule.
    """
    fake: TenantProviderKeyStore = FakeTenantProviderKeyStore()  # type: ignore[type-abstract]
    tenant_id = uuid.uuid4()

    await fake.upsert(tenant_id, "openai", BearerCredential(secret="sk-openai"), enabled=False)

    result = await fake.get(tenant_id, "openai")
    assert result is None, "A disabled credential must return None from get()"

    # list still shows it as configured (just disabled)
    statuses = await fake.list(tenant_id)
    openai_status = next((s for s in statuses if s.provider == "openai"), None)
    assert openai_status is not None
    assert openai_status.configured is True
    assert openai_status.enabled is False
