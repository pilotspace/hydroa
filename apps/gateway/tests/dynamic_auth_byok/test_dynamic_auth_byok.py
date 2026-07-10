"""Failing-first (RED) suite for dynamic-auth-byok task 3.

§3 CONTRACT (FROZEN @ v25 task-3) — DO NOT MODIFY TO MAKE TESTS PASS.

Scenarios covered (one test per §2 scenario M1–M7, R1–R3):

  M1 — BYOK_PROVIDERS constant is renamed/expanded to include bedrock+azure.
  M2 — BedrockCompletionUpstream reads credential from contextvar (not ctor);
       SigV4 Authorization Credential scope carries the tenant's region.
  M3 — BedrockCompletionUpstream session_token forwarded from BedrockCredential.
  M4 — AzureCompletionUpstream reads AzureCredential from contextvar (api_key mode).
  M5 — AzureCompletionUpstream reads AzureCredential from contextvar (aad mode).
  M6 — AzureCredential wrong-type (BearerCredential) → ProviderKeyMissing("azure").
  M7 — create_app() with NO env creds → bedrock+azure STILL in adapters
       (unconditional wiring; cache on app.state).
  R1 — resolve_provider_credential now resolves bedrock provider (no longer skips).
  R2 — resolve_provider_credential now resolves azure provider (no longer skips).
  R3 — BedrockCompletionUpstream with wrong-type credential → ProviderKeyMissing("bedrock").

TRUE-RED RULE: every new-behavior test must fail for the RIGHT reason:
  - ImportError on BYOK_PROVIDERS (renamed from BYOK_BEARER_PROVIDERS in the domain)
    or AzureADTokenProviderCache (new class not yet created).
  - AttributeError / assertion error on old constructor signatures
    (credentials/region ctor args still present → adapter ignores contextvar).
  - Wrong behavior (adapter reads env creds, not contextvar; or bedrock/azure still
    skipped in resolve_provider_credential).
DO NOT implement any src code — tests must fail as written.
"""

from __future__ import annotations

import json
from urllib.parse import unquote

import httpx
import pytest

# ---------------------------------------------------------------------------
# Stable imports — these exist from tasks 1+2; MUST succeed even in RED state
# ---------------------------------------------------------------------------
from gateway.core.egress_policy import AllowAllEgressPolicy
from gateway.proxy.domain.credential_context import (
    get_provider_credential,
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import (
    AzureCredential,
    BedrockCredential,
    BearerCredential,
    ProviderKeyMissing,
)

# ---------------------------------------------------------------------------
# RED imports — reference FUTURE symbols; will fail ImportError / AttributeError
# until task-3 BUILD ships them.
#
# Import strategy: lazy (inside each test or a local scope) to avoid a single
# ImportError killing the entire collection — each test owns its own RED reason.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Shared test constants — mirror the existing harness style
# ---------------------------------------------------------------------------

_TENANT_REGION = "eu-west-2"  # tenant-specified, NOT the env default

_BEDROCK_CRED = BedrockCredential(
    access_key_id="AKIDTENANT000000000",
    secret_access_key="tenantfakekey0000000000000000000000000000",
    region=_TENANT_REGION,
)

_BEDROCK_CRED_WITH_TOKEN = BedrockCredential(
    access_key_id="AKIDTENANT000000000",
    secret_access_key="tenantfakekey0000000000000000000000000000",
    region=_TENANT_REGION,
    session_token="TenantSessionToken12345",
)

_AZURE_API_KEY_CRED = AzureCredential(
    mode="api_key",
    endpoint="https://tenant.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"gpt-4o": "tenant-4o"},
    api_key="tenant-az-key",
)

_AZURE_AAD_CRED = AzureCredential(
    mode="aad",
    endpoint="https://tenant.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"gpt-4o": "tenant-4o"},
    tenant_id="tenant-aad-id",
    client_id="client-aad-id",
    client_secret="aad-secret",
)

_WRONG_TYPE_CRED = BearerCredential(secret="bearer-wrong-type")

_MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"

_CONVERSE_200 = {
    "output": {
        "message": {
            "role": "assistant",
            "content": [{"text": "hello"}],
        }
    },
    "stopReason": "end_turn",
    "usage": {"inputTokens": 5, "outputTokens": 3, "totalTokens": 8},
}

_AZURE_200 = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

_PAYLOAD = {
    "model": _MODEL_ID,
    "messages": [{"role": "user", "content": "hi"}],
}

_AZURE_PAYLOAD = {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "hi"}],
}


# ===========================================================================
# M1 — BYOK_PROVIDERS includes bedrock and azure
# ===========================================================================


def test_byok_providers_includes_bedrock_and_azure() -> None:
    """M1: BYOK_PROVIDERS (renamed from BYOK_BEARER_PROVIDERS) is a frozenset of ALL SEVEN
    providers: openrouter, openai, anthropic, google, bedrock, azure, minimax.

    Widened by minimax-adapter-registry (TASK.md §2) to include "minimax" — a pure
    value-set widening, never a weakening of the original task-3 assertion.

    RIGHT-REASON RED (pre task-3): AttributeError — BYOK_PROVIDERS does not exist yet in
    gateway.proxy.domain.provider_credentials; only BYOK_BEARER_PROVIDERS exists (4 providers).
    RIGHT-REASON RED (pre minimax-adapter-registry BUILD): AssertionError — "minimax" absent.
    """
    # Import the FUTURE symbol — expected to raise AttributeError or ImportError
    from gateway.proxy.domain.provider_credentials import BYOK_PROVIDERS  # type: ignore[attr-defined]

    expected = frozenset(
        {"openrouter", "openai", "anthropic", "google", "bedrock", "azure", "minimax"}
    )
    assert BYOK_PROVIDERS == expected, (
        f"BYOK_PROVIDERS must equal {expected!r}, got {BYOK_PROVIDERS!r}. "
        "bedrock, azure, and minimax must be in the set."
    )


# ===========================================================================
# M2 — Bedrock adapter reads BedrockCredential from contextvar; region in SigV4 scope
# ===========================================================================


async def test_bedrock_reads_credential_from_contextvar_sigv4_region() -> None:
    """M2: BedrockCompletionUpstream (no ctor creds/region) reads BedrockCredential
    from the contextvar per-request. The SigV4 Authorization Credential scope carries
    the tenant's region (_TENANT_REGION = 'eu-west-2'), NOT any env default.

    RIGHT-REASON RED: BedrockCompletionUpstream.__init__ still requires credentials=
    and region= positional kwargs — construction without them raises TypeError.
    Once construction is fixed, the Authorization header still uses the env region
    (not the contextvar tenant region), failing the assertion.
    """
    from gateway.proxy.infrastructure.bedrock_upstream import BedrockCompletionUpstream  # type: ignore[import]

    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, json=_CONVERSE_200)

    # Task-3 adapter: NO credentials or region ctor args; endpoint_url for test isolation
    adapter = BedrockCompletionUpstream(  # type: ignore[call-arg]
        endpoint_url=f"https://bedrock-runtime.{_TENANT_REGION}.amazonaws.com",
        default_max_tokens=4096,
        max_retries=0,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )

    tok = set_provider_credential(_BEDROCK_CRED)
    try:
        status, body = await adapter.complete(_PAYLOAD)
    finally:
        reset_provider_credential(tok)

    assert status == 200, f"Expected 200, got {status}: {body}"

    req = captured["req"]
    auth = req.headers.get("authorization", "")

    # SigV4 Authorization must start correctly
    assert auth.startswith("AWS4-HMAC-SHA256"), (
        f"Authorization must start with AWS4-HMAC-SHA256, got: {auth!r}"
    )

    # The Credential scope must carry the TENANT's region, not the env default
    assert f"/{_TENANT_REGION}/bedrock/" in auth, (
        f"SigV4 Credential scope must carry tenant region '{_TENANT_REGION}' "
        f"(from BedrockCredential.region via contextvar), got: {auth!r}"
    )

    # x-amz-date and x-amz-content-sha256 must be present
    assert "x-amz-date" in req.headers, "Missing x-amz-date header"
    assert "x-amz-content-sha256" in req.headers, "Missing x-amz-content-sha256 header"

    # After contextvar reset, credential is gone (no cross-request leak)
    assert get_provider_credential() is None


# ===========================================================================
# M3 — Session token forwarded via BedrockCredential.session_token
# ===========================================================================


async def test_bedrock_session_token_forwarded_from_credential() -> None:
    """M3: When BedrockCredential.session_token is set, the outgoing request carries
    x-amz-security-token with that value (forwarded via to_aws_credentials(), sourced
    from the contextvar — not a ctor arg).

    RIGHT-REASON RED: Same as M2 — ctor still requires credentials=/region= args.
    """
    from gateway.proxy.infrastructure.bedrock_upstream import BedrockCompletionUpstream  # type: ignore[import]

    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, json=_CONVERSE_200)

    adapter = BedrockCompletionUpstream(  # type: ignore[call-arg]
        endpoint_url=f"https://bedrock-runtime.{_TENANT_REGION}.amazonaws.com",
        default_max_tokens=4096,
        max_retries=0,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )

    tok = set_provider_credential(_BEDROCK_CRED_WITH_TOKEN)
    try:
        await adapter.complete(_PAYLOAD)
    finally:
        reset_provider_credential(tok)

    req = captured["req"]
    assert "x-amz-security-token" in req.headers, (
        "x-amz-security-token must be present when BedrockCredential.session_token is set"
    )
    assert req.headers["x-amz-security-token"] == "TenantSessionToken12345", (
        f"x-amz-security-token must equal the session_token from BedrockCredential, "
        f"got: {req.headers.get('x-amz-security-token')!r}"
    )


# ===========================================================================
# M4 — Azure adapter reads AzureCredential (api_key mode) from contextvar
# ===========================================================================


async def test_azure_api_key_mode_reads_from_contextvar() -> None:
    """M4: AzureCompletionUpstream (no ctor config/token_provider) reads AzureCredential
    in api_key mode from the contextvar. The outgoing request carries:
      - api-key: <cred.api_key value>
      - URL contains the tenant's endpoint and deployment from cred.to_azure_config()

    RIGHT-REASON RED: AzureCompletionUpstream.__init__ still requires config= kwarg.
    Once removed, the header is still sourced from the old self._config, not the contextvar.
    """
    from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream  # type: ignore[import]

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("api-key")
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=_AZURE_200)

    # Task-3 ctor: no config or token_provider; gains token_provider_cache
    # We pass a fake cache (None or stub) — the cache test is in test_azure_ad_provider_cache.py
    # edge-input-hardening §3 Part B: AllowAllEgressPolicy so this MockTransport-backed
    # adapter's fake endpoint isn't denied by the real deny-by-default production policy
    # (which would otherwise attempt real DNS resolution on a non-resolving test hostname).
    adapter = AzureCompletionUpstream(  # type: ignore[call-arg]
        max_retries=0,
        backoff_base=0.0,
        retry_deadline_s=0.0,
        egress_policy=AllowAllEgressPolicy(),
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )

    tok = set_provider_credential(_AZURE_API_KEY_CRED)
    try:
        status, body = await adapter.complete(_AZURE_PAYLOAD)
    finally:
        reset_provider_credential(tok)

    assert status == 200, f"Expected 200, got {status}: {body}"

    # api-key header must carry the TENANT's key from the credential
    assert captured["api_key"] == "tenant-az-key", (
        f"api-key header must equal AzureCredential.api_key from contextvar, "
        f"got: {captured['api_key']!r}"
    )
    # No Authorization Bearer when mode == api_key
    assert captured["authorization"] is None, (
        f"Authorization header must be absent in api_key mode, got: {captured['authorization']!r}"
    )
    # URL must be routed to the tenant's endpoint
    assert "tenant.openai.azure.com" in str(captured["url"]), (
        f"URL must use the tenant endpoint from AzureCredential.endpoint, got: {captured['url']!r}"
    )


# ===========================================================================
# M5 — Azure adapter reads AzureCredential (aad mode) from contextvar
# ===========================================================================


async def test_azure_aad_mode_reads_from_contextvar() -> None:
    """M5: AzureCompletionUpstream in aad mode reads AzureCredential from contextvar,
    calls token_provider_cache.get_or_create(cred.to_azure_ad_config()), and sends
    Authorization: Bearer <token>. No api-key header.

    RIGHT-REASON RED: AzureCompletionUpstream.__init__ still requires config=/token_provider=;
    AzureADTokenProviderCache does not exist yet (ImportError).
    """
    # Import the FUTURE cache class — expected to raise ImportError
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache  # type: ignore[attr-defined]
    from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream  # type: ignore[import]

    captured: dict[str, object] = {}

    class _FakeTokenProvider:
        async def get_token(self) -> str:
            return "aad-bearer-token"

    class _FakeCache:
        """Stub AzureADTokenProviderCache — returns a fixed provider."""

        def __init__(self) -> None:
            self.calls: list[object] = []

        def get_or_create(self, config: object) -> _FakeTokenProvider:  # type: ignore[override]
            self.calls.append(config)
            return _FakeTokenProvider()

    fake_cache = _FakeCache()

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["api_key"] = request.headers.get("api-key")
        return httpx.Response(200, json=_AZURE_200)

    # edge-input-hardening §3 Part B: AllowAllEgressPolicy so this MockTransport-backed
    # adapter's fake endpoint isn't denied by the real deny-by-default production policy.
    adapter = AzureCompletionUpstream(  # type: ignore[call-arg]
        token_provider_cache=fake_cache,  # type: ignore[arg-type]
        max_retries=0,
        backoff_base=0.0,
        retry_deadline_s=0.0,
        egress_policy=AllowAllEgressPolicy(),
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )

    tok = set_provider_credential(_AZURE_AAD_CRED)
    try:
        status, body = await adapter.complete(_AZURE_PAYLOAD)
    finally:
        reset_provider_credential(tok)

    assert status == 200, f"Expected 200, got {status}: {body}"

    # Bearer token must be present from the AAD provider
    assert captured["authorization"] == "Bearer aad-bearer-token", (
        f"Authorization must be 'Bearer aad-bearer-token' in aad mode, "
        f"got: {captured['authorization']!r}"
    )
    # No api-key when AAD
    assert captured["api_key"] is None, (
        f"api-key header must be absent in aad mode, got: {captured['api_key']!r}"
    )
    # Cache was consulted exactly once
    assert len(fake_cache.calls) == 1, (
        f"token_provider_cache.get_or_create must be called once per request, "
        f"got {len(fake_cache.calls)} calls"
    )


# ===========================================================================
# M6 — Wrong credential type for Azure → ProviderKeyMissing("azure")
# ===========================================================================


async def test_azure_wrong_credential_type_raises_provider_key_missing() -> None:
    """M6: When the contextvar holds a BearerCredential (wrong type for Azure),
    AzureCompletionUpstream raises ProviderKeyMissing("azure") — fail-closed.

    RIGHT-REASON RED: AzureCompletionUpstream.__init__ still requires config=;
    once construction is fixed, the adapter still reads from self._config
    instead of checking the contextvar type, so ProviderKeyMissing is not raised.
    """
    from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream  # type: ignore[import]

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_AZURE_200)

    adapter = AzureCompletionUpstream(  # type: ignore[call-arg]
        max_retries=0,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )

    tok = set_provider_credential(_WRONG_TYPE_CRED)
    try:
        with pytest.raises(ProviderKeyMissing) as exc_info:
            await adapter.complete(_AZURE_PAYLOAD)
    finally:
        reset_provider_credential(tok)

    err = exc_info.value
    assert err.code == "ERR_PROVIDER_KEY_MISSING", (
        f"ProviderKeyMissing.code must be 'ERR_PROVIDER_KEY_MISSING', got: {err.code!r}"
    )
    assert call_count == 0, (
        "No HTTP call must be made when credential type is wrong (fail-closed before send)"
    )


# ===========================================================================
# R3 — Wrong credential type for Bedrock → ProviderKeyMissing("bedrock")
# ===========================================================================


async def test_bedrock_wrong_credential_type_raises_provider_key_missing() -> None:
    """R3: When the contextvar holds a BearerCredential (wrong type for Bedrock),
    BedrockCompletionUpstream raises ProviderKeyMissing("bedrock") — fail-closed.

    RIGHT-REASON RED: BedrockCompletionUpstream still requires credentials= ctor arg.
    Once removed, the adapter does not check the contextvar type and raises the
    wrong exception (or no exception at all).
    """
    from gateway.proxy.infrastructure.bedrock_upstream import BedrockCompletionUpstream  # type: ignore[import]

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_CONVERSE_200)

    adapter = BedrockCompletionUpstream(  # type: ignore[call-arg]
        endpoint_url=f"https://bedrock-runtime.us-east-1.amazonaws.com",
        default_max_tokens=4096,
        max_retries=0,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )

    tok = set_provider_credential(_WRONG_TYPE_CRED)
    try:
        with pytest.raises(ProviderKeyMissing) as exc_info:
            await adapter.complete(_PAYLOAD)
    finally:
        reset_provider_credential(tok)

    err = exc_info.value
    assert err.code == "ERR_PROVIDER_KEY_MISSING", (
        f"ProviderKeyMissing.code must be 'ERR_PROVIDER_KEY_MISSING', got: {err.code!r}"
    )
    assert call_count == 0, (
        "No HTTP call must be made when credential type is wrong (fail-closed before send)"
    )


# ===========================================================================
# M7 — create_app() unconditionally registers bedrock + azure adapters
# ===========================================================================


def test_unconditional_wiring_no_env_creds() -> None:
    """M7: create_app() with NO env credentials still registers 'bedrock' and 'azure'
    in app.state.chat_adapters and provider_registry; an AzureADTokenProviderCache
    is wired on app.state.

    RIGHT-REASON RED:
    - Current wiring gates bedrock on _aws_creds (env-bound) → absent without env creds.
    - Current wiring gates azure on _azure_cfg (env-bound) → absent without env creds.
    - AzureADTokenProviderCache does not exist yet.
    """
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache  # type: ignore[attr-defined]
    from gateway.proxy.infrastructure.bedrock_upstream import BedrockCompletionUpstream  # type: ignore[import]
    from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream  # type: ignore[import]
    from gateway.main import create_app
    from gateway.core.config import Settings

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
        # NO bedrock_access_key_id / bedrock_secret_access_key
        # NO azure_api_key / azure_client_secret
    )  # type: ignore[call-arg]

    app = create_app(settings)

    # Bedrock must be present even without env creds
    chat_adapters: dict[str, object] = app.state.chat_adapters
    assert "bedrock" in chat_adapters, (
        "'bedrock' must be in app.state.chat_adapters unconditionally after task-3 BUILD. "
        "It is absent — the env-guard is still in place (pre-BUILD state)."
    )
    assert isinstance(chat_adapters["bedrock"], BedrockCompletionUpstream), (
        f"chat_adapters['bedrock'] must be BedrockCompletionUpstream, "
        f"got {type(chat_adapters['bedrock'])!r}"
    )

    # Azure chat adapter must be present even without env creds
    assert "azure" in chat_adapters, (
        "'azure' must be in app.state.chat_adapters unconditionally after task-3 BUILD. "
        "It is absent — the env-guard is still in place (pre-BUILD state)."
    )
    assert isinstance(chat_adapters["azure"], AzureCompletionUpstream), (
        f"chat_adapters['azure'] must be AzureCompletionUpstream, "
        f"got {type(chat_adapters['azure'])!r}"
    )

    # Azure embeddings provider must be present (ProviderRegistry exposes .get(), not __contains__)
    provider_registry = app.state.provider_registry
    assert provider_registry.get("azure") is not None, (
        "app.state.provider_registry.get('azure') must return an AzureEmbeddingsProvider "
        "unconditionally after task-3 BUILD."
    )

    # Bedrock embeddings provider must be present
    assert provider_registry.get("bedrock") is not None, (
        "app.state.provider_registry.get('bedrock') must return a BedrockEmbeddingsProvider "
        "unconditionally after task-3 BUILD."
    )

    # AzureADTokenProviderCache must be wired on app.state
    cache = getattr(app.state, "azure_ad_token_provider_cache", None)
    assert cache is not None, (
        "app.state.azure_ad_token_provider_cache must be set after task-3 BUILD. "
        "It is absent — AzureADTokenProviderCache not yet wired."
    )
    assert isinstance(cache, AzureADTokenProviderCache), (
        f"app.state.azure_ad_token_provider_cache must be AzureADTokenProviderCache, "
        f"got {type(cache)!r}"
    )


# ===========================================================================
# R1 — resolve_provider_credential now resolves bedrock (no longer skips)
# ===========================================================================


async def test_resolve_provider_credential_resolves_bedrock() -> None:
    """R1: After task-3, resolve_provider_credential resolves 'bedrock' via the
    resolver (BYOK_PROVIDERS now includes 'bedrock'); the contextvar is set.

    RIGHT-REASON RED: BYOK_BEARER_PROVIDERS still excludes 'bedrock'; the function
    returns None without consulting the resolver → assertion fails.
    """
    import uuid

    from gateway.proxy.application.use_cases import resolve_provider_credential

    bedrock_cred = BedrockCredential(
        access_key_id="AKIDTENANT000",
        secret_access_key="tenantkey0000000000000000000000000000000",
        region="ap-southeast-1",
    )

    class _RecordingResolver:
        def __init__(self) -> None:
            self.calls: list[tuple[object, str]] = []

        async def resolve(self, tenant_id: object, provider: str) -> BedrockCredential:
            self.calls.append((tenant_id, provider))
            return bedrock_cred

    resolver = _RecordingResolver()
    tenant_id = uuid.uuid4()

    token = await resolve_provider_credential(resolver, tenant_id, "bedrock")

    assert token is not None, (
        "resolve_provider_credential must return a contextvar token for 'bedrock' "
        "after task-3 BUILD. Got None — 'bedrock' is still being SKIPPED "
        "(BYOK_BEARER_PROVIDERS not yet updated to BYOK_PROVIDERS)."
    )
    assert resolver.calls == [(tenant_id, "bedrock")], (
        f"resolver.resolve must be called with (tenant_id, 'bedrock'), "
        f"got calls: {resolver.calls!r}"
    )

    try:
        cred = get_provider_credential()
        assert cred is bedrock_cred, (
            "contextvar must hold the resolved BedrockCredential after resolve_provider_credential"
        )
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]

    assert get_provider_credential() is None


# ===========================================================================
# R2 — resolve_provider_credential now resolves azure (no longer skips)
# ===========================================================================


async def test_resolve_provider_credential_resolves_azure() -> None:
    """R2: After task-3, resolve_provider_credential resolves 'azure' via the resolver;
    the contextvar is set with the AzureCredential.

    RIGHT-REASON RED: BYOK_BEARER_PROVIDERS still excludes 'azure'; the function
    returns None without consulting the resolver → assertion fails.
    """
    import uuid

    from gateway.proxy.application.use_cases import resolve_provider_credential

    azure_cred = AzureCredential(
        mode="api_key",
        endpoint="https://tenant.openai.azure.com",
        api_key="tenant-key",
    )

    class _RecordingResolver:
        def __init__(self) -> None:
            self.calls: list[tuple[object, str]] = []

        async def resolve(self, tenant_id: object, provider: str) -> AzureCredential:
            self.calls.append((tenant_id, provider))
            return azure_cred

    resolver = _RecordingResolver()
    tenant_id = uuid.uuid4()

    token = await resolve_provider_credential(resolver, tenant_id, "azure")

    assert token is not None, (
        "resolve_provider_credential must return a contextvar token for 'azure' "
        "after task-3 BUILD. Got None — 'azure' is still being SKIPPED."
    )
    assert resolver.calls == [(tenant_id, "azure")], (
        f"resolver.resolve must be called with (tenant_id, 'azure'), got calls: {resolver.calls!r}"
    )

    try:
        cred = get_provider_credential()
        assert cred is azure_cred, (
            "contextvar must hold the resolved AzureCredential after resolve_provider_credential"
        )
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]

    assert get_provider_credential() is None
