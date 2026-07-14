"""Failing-first (RED) suite for the DB-backed TenantProviderKeyStore (§3 CONTRACT, TASK.md §4).

Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test.
Real Redis at redis://localhost:6380/9 (via root conftest autouse isolation).
bootstrap_fresh_db() pattern mirrors oidc_tenant_config harness exactly.
All tests call the store directly (no HTTP) — this is a schema + PORT task.

TRUE-RED RULE: every test imports from:
  - gateway.proxy.domain.provider_credentials      (does NOT exist yet)
  - gateway.proxy.infrastructure.tenant_provider_key_store  (does NOT exist yet)
All tests fail on ModuleNotFoundError at collection time.

Right-reason red targets per test (all share the same primary red reason):

  test_bearer_upsert_encrypts_at_rest:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once modules exist: row in tenant_provider_keys with enabled=true; secret_enc bytes
    do NOT contain the plaintext 'sk-or-123'.)

  test_get_roundtrips_decrypted_masked:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once modules exist: get() returns BearerCredential; .get_secret_value()=='sk-or-123';
    repr() excludes the plaintext.)

  test_get_absent_returns_none:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once modules exist: get(tenant, 'anthropic') returns None when no row exists.)

  test_upsert_rotates_single_row:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once modules exist: second upsert overwrites; get() == 'sk-new'; exactly one row.)

  test_bedrock_roundtrip_to_aws_credentials:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once modules exist: upsert+get+.to_aws_credentials() equals AwsCredentials(...);
    neither plaintext secret nor access_key_id appears in secret_enc or extra_enc bytes.)

  test_azure_aad_roundtrip_converters:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once modules exist: upsert+get(.azure aad).to_azure_ad_config() + to_azure_config()
    round-trips; deployment_map preserved.)

  test_list_masked_status_no_decrypt:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once modules exist: list() returns ProviderKeyStatus objects with no secret field;
    the ciphertext in the DB is never decrypted by list().)

  test_delete_removes_and_reports:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once modules exist: delete(T,'google') returns True; second delete returns False;
    get after delete returns None.)

  test_disabled_not_resolvable:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once modules exist: upsert with enabled=false; get() returns None; list() shows
    configured=true, enabled=false.)

  test_unknown_provider_rejected:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once modules exist: upsert("cohere",...) raises ProviderCredentialError with
    code=='ERR_PROVIDER_UNKNOWN'; no row written.)

  test_missing_encryption_key_fails_closed:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once modules exist: upsert with provider_key_encryption_key='' raises
    ProviderCredentialError code=='ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE'; no row written.)

  test_corrupt_ciphertext_from_none:
    ModuleNotFoundError: No module named 'gateway.proxy.domain.provider_credentials'
    (Once modules exist: tampered secret_enc; get() raises ProviderCredentialError
    code=='ERR_PROVIDER_CREDENTIAL_CORRUPT'; __cause__ is None; row unchanged.)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app

# ---------------------------------------------------------------------------
# Modules under test — neither exists yet; import failure IS the red target.
# ---------------------------------------------------------------------------
from gateway.proxy.domain.provider_credentials import (  # type: ignore[import]
    AzureCredential,
    BearerCredential,
    BedrockCredential,
    ProviderCredentialError,
    ProviderKeyStatus,
    TenantProviderKeyStore,
)
from gateway.proxy.infrastructure.tenant_provider_key_store import (  # type: ignore[import]
    DbTenantProviderKeyStore,
)

# Existing frozen dataclasses (these DO exist)
from gateway.proxy.infrastructure.azure_ad import DEFAULT_AUTHORITY, DEFAULT_SCOPE, AzureADConfig
from gateway.proxy.infrastructure.azure_config import AzureConfig
from gateway.proxy.infrastructure.bedrock_sigv4 import AwsCredentials
from tests import _redis_env

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = _redis_env.TEST_DATABASE_URL
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"  # noqa: S105
TEST_FERNET_KEY = Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


def make_settings_with_key(encryption_key: str = TEST_FERNET_KEY) -> Settings:
    """Build Settings with a valid provider_key_encryption_key."""
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        provider_key_encryption_key=encryption_key,
    )


def make_settings_no_key() -> Settings:
    """Build Settings with provider_key_encryption_key unset (empty string)."""
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        provider_key_encryption_key="",
    )


# ---------------------------------------------------------------------------
# DB bootstrap helpers (mirrors oidc_tenant_config harness)
# ---------------------------------------------------------------------------


async def bootstrap_fresh_db(settings: Settings) -> tuple[Any, Any]:
    """Create a fresh schema; return (engine, sessionmaker).

    NOTE: tenant_provider_keys will not be in Base.metadata until the ORM exists —
    that is fine; the ModuleNotFoundError fires before we reach this helper.
    This mirrors the oidc_tenant_config bootstrap_fresh_db exactly.
    """
    bootstrap_app = create_app(settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap_app.state.engine.dispose()
    return engine, bootstrap_app.state.sessionmaker


async def insert_tenant_row(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Insert a minimal tenants row to satisfy FK tenant_provider_keys.tenant_id → tenants.id.

    Uses raw SQL to avoid coupling to the ORM; mirrors the oidc_tenant_config signup pattern
    for FK setup, without requiring HTTP.
    """
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, created_at, updated_at) "
            "VALUES (:id, :name, now(), now()) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": tenant_id, "name": f"test-tenant-{tenant_id}"},
    )
    await session.commit()


def build_store(sessionmaker: Any, settings: Settings) -> DbTenantProviderKeyStore:
    """Instantiate the DB store with the given sessionmaker and settings."""
    return DbTenantProviderKeyStore(sessionmaker=sessionmaker, settings=settings)


# ---------------------------------------------------------------------------
# S1: Upsert encrypts a Bearer credential at rest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bearer_upsert_encrypts_at_rest() -> None:
    """upsert(T,'openrouter', BearerCredential(secret='sk-or-123')) -> row with enabled=true;
    secret_enc bytes do NOT contain 'sk-or-123' plaintext.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Once modules exist: assert the actual ciphertext bytes do not contain the raw secret.
    Security assertion: plaintext bytes MUST be absent from the stored ciphertext column.
    """
    settings = make_settings_with_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    tenant_id = uuid.uuid4()
    raw_secret = "sk-or-123"

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings)
    await store.upsert(tenant_id, "openrouter", BearerCredential(secret=raw_secret))

    # Verify the row exists with encryption in place
    async with sm() as session:
        row = (
            await session.execute(
                text(
                    "SELECT secret_enc, extra_enc, enabled "
                    "FROM tenant_provider_keys "
                    "WHERE tenant_id = :tid AND provider = 'openrouter'"
                ),
                {"tid": tenant_id},
            )
        ).fetchone()

    assert row is not None, "Row must exist after upsert"
    assert row[2] is True, "enabled must be True after upsert"

    secret_enc: bytes = row[0]
    assert isinstance(secret_enc, bytes), "secret_enc must be bytes (BYTEA)"

    # SECURITY ASSERTION: plaintext must not appear in ciphertext bytes
    assert raw_secret.encode() not in secret_enc, (
        f"SECURITY VIOLATION: plaintext '{raw_secret}' found in secret_enc ciphertext bytes"
    )
    assert raw_secret not in secret_enc.decode("latin-1", errors="replace"), (
        f"SECURITY VIOLATION: plaintext '{raw_secret}' decodable from secret_enc"
    )

    await engine.dispose()


# ---------------------------------------------------------------------------
# S2: Get round-trips a stored credential decrypted in memory, secret masked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_roundtrips_decrypted_masked() -> None:
    """upsert then get -> BearerCredential; .get_secret_value()=='sk-or-123'; repr excludes it.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Asserts both the round-trip value AND the SecretStr masking in repr.
    """
    settings = make_settings_with_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    tenant_id = uuid.uuid4()
    raw_secret = "sk-or-123"

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings)
    await store.upsert(tenant_id, "openrouter", BearerCredential(secret=raw_secret))

    result = await store.get(tenant_id, "openrouter")

    assert result is not None, "get() must return a credential after upsert"
    assert isinstance(result, BearerCredential), f"Expected BearerCredential, got {type(result)}"
    # Value round-trip
    assert result.secret.get_secret_value() == raw_secret, (
        f"Expected '{raw_secret}', got '{result.secret.get_secret_value()}'"
    )
    # SecretStr masking
    result_repr = repr(result)
    assert raw_secret not in result_repr, f"SECURITY: raw secret found in repr: {result_repr}"

    await engine.dispose()


# ---------------------------------------------------------------------------
# S3: Get returns None for an unconfigured provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_absent_returns_none() -> None:
    """get(T, 'anthropic') returns None when no row exists.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    """
    settings = make_settings_with_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    tenant_id = uuid.uuid4()

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings)
    result = await store.get(tenant_id, "anthropic")

    assert result is None, f"Expected None for unconfigured provider, got {result}"

    await engine.dispose()


# ---------------------------------------------------------------------------
# S4: Upsert rotates an existing credential without duplicating the row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_rotates_single_row() -> None:
    """upsert sk-old then sk-new -> get()==sk-new AND exactly one row in table.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Verifies ON CONFLICT DO UPDATE semantics — rotation replaces rather than duplicating.
    """
    settings = make_settings_with_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    tenant_id = uuid.uuid4()

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings)

    await store.upsert(tenant_id, "openai", BearerCredential(secret="sk-old"))
    await store.upsert(tenant_id, "openai", BearerCredential(secret="sk-new"))

    result = await store.get(tenant_id, "openai")
    assert result is not None
    assert result.secret.get_secret_value() == "sk-new", (
        f"Expected 'sk-new' after rotation, got '{result.secret.get_secret_value()}'"
    )

    # Exactly one row must exist — no duplicate from the second upsert
    async with sm() as session:
        count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM tenant_provider_keys "
                    "WHERE tenant_id = :tid AND provider = 'openai'"
                ),
                {"tid": tenant_id},
            )
        ).scalar()

    assert count == 1, f"Expected exactly 1 row after rotation, got {count}"

    await engine.dispose()


# ---------------------------------------------------------------------------
# S5: Bedrock SigV4 credential round-trips and converts to AwsCredentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bedrock_roundtrip_to_aws_credentials() -> None:
    """upsert+get(bedrock).to_aws_credentials() round-trips; no plaintext in secret_enc/extra_enc.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Security assertion: neither 's3cr3t' nor 'AKIAEX' may appear as plaintext bytes
    in either secret_enc or extra_enc columns.
    """
    settings = make_settings_with_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    tenant_id = uuid.uuid4()
    raw_secret = "s3cr3t"
    access_key_id = "AKIAEX"

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings)
    await store.upsert(
        tenant_id,
        "bedrock",
        BedrockCredential(
            access_key_id=access_key_id,
            secret_access_key=raw_secret,
            region="us-east-1",
        ),
    )

    result = await store.get(tenant_id, "bedrock")
    assert result is not None, "Expected BedrockCredential back from get()"
    assert isinstance(result, BedrockCredential)

    aws_creds = result.to_aws_credentials()
    expected = AwsCredentials(
        access_key_id=access_key_id,
        secret_access_key=raw_secret,
        region="us-east-1",
        session_token=None,
    )
    assert aws_creds == expected, f"AwsCredentials mismatch: {aws_creds!r} != {expected!r}"

    # SECURITY: no plaintext in stored columns
    async with sm() as session:
        row = (
            await session.execute(
                text(
                    "SELECT secret_enc, extra_enc FROM tenant_provider_keys "
                    "WHERE tenant_id = :tid AND provider = 'bedrock'"
                ),
                {"tid": tenant_id},
            )
        ).fetchone()

    assert row is not None
    secret_enc: bytes = row[0]
    extra_enc: bytes | None = row[1]

    assert raw_secret.encode() not in secret_enc, (
        "SECURITY VIOLATION: secret_access_key plaintext in secret_enc"
    )
    assert access_key_id.encode() not in secret_enc, (
        "SECURITY VIOLATION: access_key_id plaintext in secret_enc"
    )
    if extra_enc is not None:
        assert raw_secret.encode() not in extra_enc, (
            "SECURITY VIOLATION: secret_access_key plaintext in extra_enc"
        )
        assert access_key_id.encode() not in extra_enc, (
            "SECURITY VIOLATION: access_key_id plaintext in extra_enc"
        )

    await engine.dispose()


# ---------------------------------------------------------------------------
# S6: Azure AAD credential round-trips with both converters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_azure_aad_roundtrip_converters() -> None:
    """upsert+get(azure aad) converters round-trip; deployment_map preserved.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    This is the envelope stress test — Azure's dual-mode shape with the richest fields.
    """
    settings = make_settings_with_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    tenant_id = uuid.uuid4()
    deployment_map = {"gpt-4o": "my-deploy"}

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings)
    await store.upsert(
        tenant_id,
        "azure",
        AzureCredential(
            mode="aad",
            endpoint="https://x.openai.azure.com",
            api_version="2024-10-21",
            deployment_map=deployment_map,
            tenant_id="tid",
            client_id="cid",
            client_secret="csecret",
            scope=DEFAULT_SCOPE,
            authority=DEFAULT_AUTHORITY,
        ),
    )

    result = await store.get(tenant_id, "azure")
    assert result is not None, "Expected AzureCredential back from get()"
    assert isinstance(result, AzureCredential)

    # to_azure_ad_config() round-trip
    ad_config = result.to_azure_ad_config()
    assert isinstance(ad_config, AzureADConfig)
    assert ad_config.tenant_id == "tid"
    assert ad_config.client_id == "cid"
    assert ad_config.client_secret == "csecret"

    # to_azure_config() round-trip with deployment_map preservation
    azure_config = result.to_azure_config()
    assert isinstance(azure_config, AzureConfig)
    assert dict(azure_config.deployment_map) == deployment_map

    await engine.dispose()


@pytest.mark.asyncio
async def test_azure_api_key_roundtrip_converters() -> None:
    """upsert+get(azure api_key) round-trips; api_key masked + ciphertext at rest.

    Closes the VERIFY-gate coverage gap (2026-06-16, Tin-approved): the aad DB path
    was tested but the api_key encrypt->decrypt round-trip was not. Mirrors
    test_azure_aad_roundtrip_converters for the api_key discriminator, and adds the
    at-rest secrecy assertion (plaintext api_key absent from secret_enc bytes).
    """
    settings = make_settings_with_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    tenant_id = uuid.uuid4()
    deployment_map = {"gpt-4o": "my-deploy"}
    raw_api_key = "azure-api-secret-xyz"

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings)
    await store.upsert(
        tenant_id,
        "azure",
        AzureCredential(
            mode="api_key",
            endpoint="https://x.openai.azure.com",
            api_version="2024-10-21",
            deployment_map=deployment_map,
            api_key=raw_api_key,
        ),
    )

    # SECURITY: api_key plaintext must be absent from the stored ciphertext, and the
    # auth_mode discriminator must persist so get() reconstructs the api_key branch.
    async with sm() as session:
        row = (
            await session.execute(
                text(
                    "SELECT secret_enc, auth_mode "
                    "FROM tenant_provider_keys "
                    "WHERE tenant_id = :tid AND provider = 'azure'"
                ),
                {"tid": tenant_id},
            )
        ).fetchone()
    assert row is not None, "Row must exist after upsert"
    secret_enc: bytes = row[0]
    assert isinstance(secret_enc, bytes), "secret_enc must be bytes (BYTEA)"
    assert raw_api_key.encode() not in secret_enc, (
        f"SECURITY VIOLATION: plaintext api_key '{raw_api_key}' found in secret_enc"
    )
    assert row[1] == "api_key", "auth_mode discriminator must persist as 'api_key'"

    # Round-trip: get() returns a typed AzureCredential in api_key mode
    result = await store.get(tenant_id, "azure")
    assert result is not None, "Expected AzureCredential back from get()"
    assert isinstance(result, AzureCredential)
    assert result.mode == "api_key"

    # to_azure_config() round-trip: api_key recovered, endpoint + deployment_map preserved
    azure_config = result.to_azure_config()
    assert isinstance(azure_config, AzureConfig)
    assert azure_config.api_key == raw_api_key
    assert azure_config.endpoint == "https://x.openai.azure.com"
    assert dict(azure_config.deployment_map) == deployment_map

    await engine.dispose()


# ---------------------------------------------------------------------------
# S7: List reports per-provider status without decrypting or exposing plaintext
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_masked_status_no_decrypt() -> None:
    """list(T) returns ProviderKeyStatus entries with no secret field present.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Contract: list() NEVER decrypts; the result carries only (provider, configured,
    enabled, auth_mode, updated_at); no secret value anywhere.
    """
    settings = make_settings_with_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    tenant_id = uuid.uuid4()

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings)
    await store.upsert(tenant_id, "openrouter", BearerCredential(secret="sk-or-secret"))
    # Insert openai as disabled
    await store.upsert(
        tenant_id, "openai", BearerCredential(secret="sk-openai-secret"), enabled=False
    )

    statuses = await store.list(tenant_id)

    assert len(statuses) == 2, f"Expected 2 status entries, got {len(statuses)}"

    by_provider = {s.provider: s for s in statuses}
    assert "openrouter" in by_provider, "openrouter must appear in list"
    assert "openai" in by_provider, "openai must appear in list"

    or_status = by_provider["openrouter"]
    assert isinstance(or_status, ProviderKeyStatus)
    assert or_status.configured is True
    assert or_status.enabled is True

    openai_status = by_provider["openai"]
    assert openai_status.configured is True
    assert openai_status.enabled is False

    # SECURITY: no secret field on any status object
    for status in statuses:
        assert not hasattr(status, "secret"), "ProviderKeyStatus must not have a secret field"
        status_dict = status.model_dump()
        assert "secret" not in status_dict, f"'secret' found in status dict: {status_dict}"
        # No raw secret value appears anywhere in the repr
        raw_body = repr(status)
        assert "sk-or-secret" not in raw_body
        assert "sk-openai-secret" not in raw_body

    await engine.dispose()


# ---------------------------------------------------------------------------
# S8: Delete removes a credential and reports whether it existed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_and_reports() -> None:
    """delete(T,'google') -> True + get returns None; second delete -> False.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    """
    settings = make_settings_with_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    tenant_id = uuid.uuid4()

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings)
    await store.upsert(tenant_id, "google", BearerCredential(secret="google-key"))

    # First delete — must report True
    first = await store.delete(tenant_id, "google")
    assert first is True, "First delete must return True (row existed)"

    # get after delete — must return None
    after = await store.get(tenant_id, "google")
    assert after is None, "get() must return None after delete"

    # Second delete — must report False (row no longer exists)
    second = await store.delete(tenant_id, "google")
    assert second is False, "Second delete must return False (row already gone)"

    await engine.dispose()


# ---------------------------------------------------------------------------
# S9: A disabled credential is not resolvable via get()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_not_resolvable() -> None:
    """upsert with enabled=false; get() returns None; list() shows configured=true, enabled=false.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Contract: enabled=false ≡ unconfigured-for-resolution (task-2 fail-closes on disabled).
    """
    settings = make_settings_with_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    tenant_id = uuid.uuid4()

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings)
    await store.upsert(tenant_id, "openai", BearerCredential(secret="sk-disabled"), enabled=False)

    # get() must return None (disabled = unconfigured for resolution)
    result = await store.get(tenant_id, "openai")
    assert result is None, "Disabled credential must return None from get()"

    # list() still shows it as configured=true, enabled=false
    statuses = await store.list(tenant_id)
    openai_status = next((s for s in statuses if s.provider == "openai"), None)
    assert openai_status is not None, "Disabled credential must appear in list()"
    assert openai_status.configured is True
    assert openai_status.enabled is False

    await engine.dispose()


# ---------------------------------------------------------------------------
# S10: Unknown provider is rejected (ERR_PROVIDER_UNKNOWN)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_provider_rejected() -> None:
    """upsert(T,'cohere', ...) -> ProviderCredentialError.code=='ERR_PROVIDER_UNKNOWN'; no row.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Once module exists: the store must validate the provider against the bounded value-set
    BEFORE any DB I/O; no row must be written.
    """
    settings = make_settings_with_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    tenant_id = uuid.uuid4()

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings)

    with pytest.raises(ProviderCredentialError) as exc_info:
        await store.upsert(tenant_id, "cohere", BearerCredential(secret="cohere-key"))

    assert exc_info.value.code == "ERR_PROVIDER_UNKNOWN", (
        f"Expected code 'ERR_PROVIDER_UNKNOWN', got {exc_info.value.code!r}"
    )

    # No row must have been written (side-effect-unchanged clause)
    async with sm() as session:
        count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM tenant_provider_keys "
                    "WHERE tenant_id = :tid AND provider = 'cohere'"
                ),
                {"tid": tenant_id},
            )
        ).scalar()

    assert count == 0, f"Expected 0 rows for unknown provider, got {count}"

    await engine.dispose()


# ---------------------------------------------------------------------------
# S11: Missing encryption key fails closed (ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_encryption_key_fails_closed() -> None:
    """provider_key_encryption_key='' -> upsert raises ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE; no row.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Security assertion: the store must fail closed BEFORE any row I/O when the key is absent;
    no plaintext must be persisted.
    """
    settings_no_key = make_settings_no_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings_no_key)

    tenant_id = uuid.uuid4()

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings_no_key)

    with pytest.raises(ProviderCredentialError) as exc_info:
        await store.upsert(tenant_id, "openrouter", BearerCredential(secret="sk"))

    assert exc_info.value.code == "ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE", (
        f"Expected 'ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE', got {exc_info.value.code!r}"
    )

    # SECURITY: no row must be written and no plaintext must appear in the table
    async with sm() as session:
        count = (
            await session.execute(
                text("SELECT COUNT(*) FROM tenant_provider_keys WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
        ).scalar()

    assert count == 0, (
        f"SECURITY VIOLATION: row written despite missing encryption key; count={count}"
    )

    await engine.dispose()


# ---------------------------------------------------------------------------
# S12: Corrupt ciphertext fails closed, __cause__ is None, row unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrupt_ciphertext_from_none() -> None:
    """Tamper secret_enc; get() raises ERR_PROVIDER_CREDENTIAL_CORRUPT; __cause__ is None; row intact.

    RIGHT-REASON RED: ModuleNotFoundError on gateway.proxy.domain.provider_credentials.
    Security assertions:
    - The error code is 'ERR_PROVIDER_CREDENTIAL_CORRUPT'.
    - __cause__ is None (raised `from None` per v22 project-wide secret-chain floor).
      This ensures no ciphertext/key/exception from the crypto layer can be inspected
      by a crash reporter or chained-traceback scanner.
    - The DB row is left unchanged (the tampered ciphertext is still there, not deleted).
    """
    settings = make_settings_with_key()
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    tenant_id = uuid.uuid4()

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)

    store = build_store(sm, settings)
    await store.upsert(tenant_id, "openrouter", BearerCredential(secret="original-secret"))

    # Tamper the secret_enc to be invalid Fernet ciphertext
    tampered_ciphertext = b"NOT-A-VALID-FERNET-TOKEN-XXXX1234"
    async with sm() as session:
        await session.execute(
            text(
                "UPDATE tenant_provider_keys SET secret_enc = :tampered "
                "WHERE tenant_id = :tid AND provider = 'openrouter'"
            ),
            {"tampered": tampered_ciphertext, "tid": tenant_id},
        )
        await session.commit()

    # get() must raise ProviderCredentialError with the correct code
    with pytest.raises(ProviderCredentialError) as exc_info:
        await store.get(tenant_id, "openrouter")

    error = exc_info.value
    assert error.code == "ERR_PROVIDER_CREDENTIAL_CORRUPT", (
        f"Expected 'ERR_PROVIDER_CREDENTIAL_CORRUPT', got {error.code!r}"
    )

    # SECURITY: __cause__ must be None (raised `from None`)
    # This is the v22 secret-chain floor: the InvalidToken exception from Fernet
    # carries the ciphertext; stripping __cause__ prevents leakage to crash reporters.
    assert error.__cause__ is None, (
        f"SECURITY VIOLATION: __cause__ is not None: {error.__cause__!r}. "
        "The error must be raised `from None` to strip the Fernet InvalidToken chain "
        "which could carry ciphertext/key material."
    )

    # DB row must be UNCHANGED (tampered ciphertext still in place, not deleted)
    async with sm() as session:
        row = (
            await session.execute(
                text(
                    "SELECT secret_enc FROM tenant_provider_keys "
                    "WHERE tenant_id = :tid AND provider = 'openrouter'"
                ),
                {"tid": tenant_id},
            )
        ).fetchone()

    assert row is not None, "Row must still exist after corrupt-decrypt attempt"
    assert row[0] == tampered_ciphertext, (
        "Row must be unchanged (tampered ciphertext must remain intact)"
    )

    await engine.dispose()
