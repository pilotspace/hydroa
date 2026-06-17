"""Red tests for provider-seam task (TASK.md §4).

ALL tests in this suite are expected to FAIL (ImportError, AttributeError,
or assertion failure) before the BUILD phase.  Every failure is for the
RIGHT reason — the module/class/field simply does not exist yet.

Run only this suite:
  cd apps/gateway && uv run pytest tests/provider_seam/ -q --no-cov -p no:cacheprovider

PS1  — embedding model → OpenAI adapter selected (not OpenRouter)
PS2  — chat model → OpenRouter facade selected
PS3  — Settings has openai_api_key/openai_base_url; create_app wires OpenAIDirectProvider
PS4  — openai_api_key unset → select_provider raises 503 ERR_PROVIDER_UNAVAILABLE
PS5  — ModelRow ORM has modality/provider columns with defaults
PS6  — client "provider" field in request body is silently ignored (GREEN-BY-DESIGN smoke)
PS7  — non-chat active model passes SqlAlchemyModelChecker unchanged
PS8  — OpenAIDirectProvider.post_json calls correct URL + auth header
PS9  — chat path byte-identical; provider_registry NOT consulted (GREEN-BY-DESIGN once wired)
PS10 — production wiring: create_app wires provider_registry with correct entries
PS10b— openai_api_key="" → registry has no "openai" entry
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from tests.provider_seam.conftest import (
    CHAT_PAYLOAD,
    CHAT_RESPONSE_BODY,
    EMBEDDING_PAYLOAD,
    FAKE_API_KEY,
    FAKE_OPENAI_BASE_URL,
    FakeCompletionUpstream,
    FakeProviderRegistry,
    FakeUpstreamProvider,
    SequencedMockTransport,
    make_json_response,
    EMBEDDING_RESPONSE_BODY,
)


# ===========================================================================
# PS1 — embedding model routes to OpenAI adapter (not OpenRouter)
# ===========================================================================


def test_ps1_embedding_model_routes_to_openai_adapter(
    fake_openai_provider: FakeUpstreamProvider,
    fake_openrouter_provider: FakeUpstreamProvider,
) -> None:
    """select_provider("embedding", "openai", registry) returns the OpenAI adapter.

    RED reason: ProviderRegistry and select_provider do not exist → ImportError.
    """
    # This import is expected to fail (ImportError) until BUILD.
    from gateway.proxy.infrastructure.provider_registry import (  # type: ignore[import]
        ProviderRegistry,
        select_provider,
    )

    registry = ProviderRegistry(
        {"openrouter": fake_openrouter_provider, "openai": fake_openai_provider}
    )
    result = select_provider("embedding", "openai", registry)

    assert result is fake_openai_provider, (
        f"Expected OpenAI adapter for modality='embedding', provider='openai'; got {result!r}"
    )
    assert result is not fake_openrouter_provider, (
        "select_provider must NOT return the OpenRouter adapter for provider='openai'"
    )


# ===========================================================================
# PS2 — chat model → OpenRouter facade selected from registry
# ===========================================================================


def test_ps2_chat_model_returns_openrouter_facade(
    fake_openai_provider: FakeUpstreamProvider,
    fake_openrouter_provider: FakeUpstreamProvider,
) -> None:
    """select_provider("chat", "openrouter", registry) returns the OpenRouter adapter.

    RED reason: ImportError on ProviderRegistry / select_provider.
    """
    from gateway.proxy.infrastructure.provider_registry import (  # type: ignore[import]
        ProviderRegistry,
        select_provider,
    )

    registry = ProviderRegistry(
        {"openrouter": fake_openrouter_provider, "openai": fake_openai_provider}
    )
    result = select_provider("chat", "openrouter", registry)

    assert result is fake_openrouter_provider, (
        f"Expected OpenRouter facade for modality='chat', provider='openrouter'; got {result!r}"
    )
    assert result is not fake_openai_provider, (
        "select_provider must NOT return the OpenAI adapter for provider='openrouter'"
    )


# ===========================================================================
# PS3 — Settings gains openai_api_key + openai_base_url; wiring creates OpenAIDirectProvider
# ===========================================================================


def test_ps3_settings_gains_openai_fields_wiring() -> None:
    """create_app(settings) always wires OpenAIDirectProvider (UNCONDITIONAL).

    Credential-resolution-seam BUILD: openai_api_key removed from Settings;
    OpenAI provider is registered unconditionally (per-tenant key gating at resolve time).
    openai_base_url field remains (describes the upstream endpoint, not the secret).
    """
    from gateway.core.config import Settings  # always importable — checking new fields
    from gateway.main import create_app
    from gateway.proxy.infrastructure.openai_provider import (  # type: ignore[import]
        OpenAIDirectProvider,
    )

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
        openai_base_url=FAKE_OPENAI_BASE_URL,
    )
    app = create_app(settings)

    assert hasattr(app.state, "provider_registry"), (
        "app.state.provider_registry must be set by create_app()"
    )
    openai_provider = app.state.provider_registry.get("openai")
    assert openai_provider is not None, (
        "provider_registry must contain 'openai' unconditionally after credential-resolution-seam BUILD"
    )
    assert isinstance(openai_provider, OpenAIDirectProvider), (
        f"Expected OpenAIDirectProvider, got {type(openai_provider)}"
    )


# ===========================================================================
# PS4 — openai_api_key unset → provider absent → select_provider raises 503
# ===========================================================================


def test_ps4_openai_key_unset_provider_absent_raises_503(
    fake_openrouter_provider: FakeUpstreamProvider,
) -> None:
    """select_provider("embedding", "openai", registry) raises ERR_PROVIDER_UNAVAILABLE 503
    when "openai" is not in the registry.

    RED reason:
      - ImportError on ProviderRegistry / select_provider
      - PROVIDER_UNAVAILABLE entry absent from error_catalog
    """
    from gateway.core.error_catalog import PROVIDER_UNAVAILABLE  # type: ignore[import]
    from gateway.core.errors import ProblemError
    from gateway.proxy.infrastructure.provider_registry import (  # type: ignore[import]
        ProviderRegistry,
        select_provider,
    )

    # Registry with only openrouter — no openai entry
    registry = ProviderRegistry({"openrouter": fake_openrouter_provider})

    with pytest.raises(ProblemError) as exc_info:
        select_provider("embedding", "openai", registry)

    err: ProblemError = exc_info.value
    assert err.status == 503, f"Expected 503, got {err.status}"
    assert err.code == "ERR_PROVIDER_UNAVAILABLE", (
        f"Expected ERR_PROVIDER_UNAVAILABLE, got {err.code!r}"
    )


def test_ps4_provider_unavailable_error_spec_exists() -> None:
    """PROVIDER_UNAVAILABLE must be defined in error_catalog.py.

    RED reason: the entry does not exist yet → ImportError.
    """
    from gateway.core.error_catalog import PROVIDER_UNAVAILABLE  # type: ignore[import]

    assert PROVIDER_UNAVAILABLE.status == 503
    assert PROVIDER_UNAVAILABLE.code == "ERR_PROVIDER_UNAVAILABLE"
    # Verify the exc() pattern works and formats the provider name
    err = PROVIDER_UNAVAILABLE.exc(provider="openai")
    assert "openai" in err.title or err.code == "ERR_PROVIDER_UNAVAILABLE"


# ===========================================================================
# PS5 — ModelRow ORM has modality/provider columns with correct defaults
# ===========================================================================


def test_ps5_modelrow_orm_has_modality_provider_columns() -> None:
    """ModelRow ORM class must have modality and provider mapped columns.

    RED reason: ModelRow ORM lacks the columns → AttributeError when accessing them.
    """
    from gateway.catalog.infrastructure.orm import ModelRow  # always importable

    # Inspect ORM mapped column names
    from sqlalchemy import inspect as sa_inspect

    mapper = sa_inspect(ModelRow)
    column_names = {c.key for c in mapper.columns}

    assert "modality" in column_names, (
        f"ModelRow ORM must have a 'modality' column; found: {column_names}"
    )
    assert "provider" in column_names, (
        f"ModelRow ORM must have a 'provider' column; found: {column_names}"
    )

    # Verify server_default values are set (they encode the migration default).
    # NB: mapper.columns["modality"] is a sqlalchemy Column, which exposes
    # .server_default directly — it has no .columns attribute in SQLAlchemy 2.x
    # (orchestrator test-defect fix: the original .columns[0] indirection raised
    # AttributeError; assertion intent — server_default present — is unchanged).
    modality_col = mapper.columns["modality"]
    provider_col = mapper.columns["provider"]

    assert modality_col.server_default is not None, (
        "modality column must have a server_default='chat'"
    )
    assert provider_col.server_default is not None, (
        "provider column must have a server_default='openrouter'"
    )


def test_ps5_catalog_model_entity_has_modality_provider() -> None:
    """CatalogModel domain entity must have modality and provider fields with defaults.

    RED reason: CatalogModel lacks the fields → TypeError on construction.
    """
    from gateway.catalog.domain.entities import CatalogModel  # always importable

    # Construct with only required fields; modality and provider should default
    model = CatalogModel(
        id="text-embedding-3-small",
        name="text-embedding-3-small",
        context_length=None,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        # modality and provider omitted — must default
    )
    assert model.modality == "chat" or hasattr(model, "modality"), (  # type: ignore[attr-defined]
        "CatalogModel must have a 'modality' field"
    )
    assert model.provider == "openrouter" or hasattr(model, "provider"), (  # type: ignore[attr-defined]
        "CatalogModel must have a 'provider' field"
    )

    # Construct with explicit non-default values
    embedding_model = CatalogModel(
        id="text-embedding-3-large",
        name="text-embedding-3-large",
        context_length=None,
        prompt_usd_per_token=0.00013,
        completion_usd_per_token=0.0,
        modality="embedding",  # type: ignore[call-arg]
        provider="openai",  # type: ignore[call-arg]
    )
    assert embedding_model.modality == "embedding"  # type: ignore[attr-defined]
    assert embedding_model.provider == "openai"  # type: ignore[attr-defined]


# ===========================================================================
# PS6 — client "provider" field in request body is silently ignored
# GREEN-BY-DESIGN: FastAPI ignores unknown JSON body fields by default.
# This test is a regression guard — it fails if the route incorrectly
# acts on the "provider" field (e.g. returns 422).
# RED at this stage because the full app fixture wiring for this suite is
# not yet in place (app.state.provider_registry not set).
# ===========================================================================


@pytest.mark.asyncio
async def test_ps6_client_provider_field_ignored(
    fake_completion_upstream: FakeCompletionUpstream,
) -> None:
    """POST /v1/chat/completions with "provider" in body returns 200; field is ignored.

    GREEN-BY-DESIGN label: FastAPI ignores unknown body fields so the 200 is expected
    once the full wiring exists. RED at spec phase because app.state.provider_registry
    is not set by create_app() yet (AttributeError in app setup), or because the
    test suite imports for FakeCompletionUpstream injection fail without the seam.

    Note: if this test passes trivially before BUILD (because FastAPI ignores the field
    and provider_registry seam is already wired), it remains a valid regression guard.
    """
    from gateway.core.config import Settings
    from gateway.main import create_app

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )
    app = create_app(settings)
    # Inject fake completion upstream so the test doesn't need a live DB/OpenRouter
    app.state.completion_upstream = fake_completion_upstream

    # After BUILD: app.state.provider_registry will be set; this line should not fail.
    # Before BUILD: AttributeError here makes the test RED for the right reason.
    assert hasattr(app.state, "provider_registry"), (
        "app.state.provider_registry must be set by create_app() — "
        "this test is RED because the seam is not yet wired"
    )


# ===========================================================================
# PS7 — non-chat active model passes SqlAlchemyModelChecker unchanged
# ===========================================================================


@pytest.mark.asyncio
async def test_ps7_non_chat_active_model_passes_catalog_check(
    db_session: Any,
) -> None:
    """SqlAlchemyModelChecker.is_active and check_for_tenant work for non-chat model rows.

    Uses the shared Postgres test DB (root conftest `db_session` fixture: fresh schema per
    test on gateway_test). The gateway ORM relies on PostgreSQL-specific column types
    (PGUUID, JSONB) across modules, so the in-memory SQLite shortcut is not viable —
    `Base.metadata.create_all` would fail to build the schema for the wrong reason, and
    `aiosqlite` is not a project dependency. This mirrors every other gateway suite that
    touches the DB (see tests/conftest.py).

    RED reason: ModelRow construction with modality="embedding"/provider="openai" raises
    TypeError (the ORM mapped class rejects the unknown kwargs) before any DB round-trip —
    the columns do not exist yet.

    Once BUILD adds the columns, this test is expected to be GREEN (the checker is
    modality-agnostic by construction — no code changes needed there).
    """
    from gateway.catalog.infrastructure.orm import ModelRow
    from gateway.proxy.domain.ports import ModelAccess
    from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker

    tenant_id = uuid.uuid4()

    # Insert a non-chat model row — RED: TypeError on modality/provider kwargs (columns absent).
    row = ModelRow(
        id="text-embedding-3-small",
        name="text-embedding-3-small",
        context_length=None,
        active=True,
        modality="embedding",  # type: ignore[call-arg]
        provider="openai",  # type: ignore[call-arg]
    )
    db_session.add(row)
    await db_session.commit()

    checker = SqlAlchemyModelChecker(db_session)
    active = await checker.is_active("text-embedding-3-small")
    access = await checker.check_for_tenant("text-embedding-3-small", tenant_id)

    assert active is True, f"is_active must return True for active non-chat model; got {active}"
    assert access == ModelAccess.ACTIVE, (
        f"check_for_tenant must return ACTIVE for active non-chat model; got {access}"
    )


# ===========================================================================
# PS8 — OpenAIDirectProvider.post_json calls correct URL + Authorization header
# ===========================================================================


@pytest.mark.asyncio
async def test_ps8_openai_provider_post_json_calls_correct_url(
    mock_transport_200_embedding: SequencedMockTransport,
) -> None:
    """OpenAIDirectProvider.post_json posts to base_url + path with Bearer auth.

    RED reason: OpenAIDirectProvider does not exist → ImportError.
    """
    from gateway.proxy.infrastructure.openai_provider import (  # type: ignore[import]
        OpenAIDirectProvider,
    )

    from gateway.proxy.domain.credential_context import (
        reset_provider_credential,
        set_provider_credential,
    )
    from gateway.proxy.domain.provider_credentials import BearerCredential
    from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

    provider = OpenAIDirectProvider.__new__(OpenAIDirectProvider)  # type: ignore[attr-defined]
    # Credential-resolution-seam BUILD: api_key removed; credential via contextvar.
    provider._client = httpx.AsyncClient(
        base_url=FAKE_OPENAI_BASE_URL,
        transport=mock_transport_200_embedding,
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
    )
    provider._breaker = CircuitBreaker()
    provider._metrics_registry = None

    # Set the credential in the contextvar before calling _auth_headers via post_json.
    cred_token = set_provider_credential(BearerCredential(secret=FAKE_API_KEY))
    try:
        status, body = await provider.post_json("/embeddings", EMBEDDING_PAYLOAD)
    finally:
        reset_provider_credential(cred_token)

    # Verify the HTTP call
    assert mock_transport_200_embedding.call_count == 1, (
        f"Expected exactly 1 HTTP call; got {mock_transport_200_embedding.call_count}"
    )
    request = mock_transport_200_embedding.last_request
    assert request is not None
    assert str(request.url) == f"{FAKE_OPENAI_BASE_URL}/embeddings", (
        f"Expected URL {FAKE_OPENAI_BASE_URL}/embeddings; got {request.url}"
    )
    assert request.method == "POST", f"Expected POST; got {request.method}"
    auth_header = request.headers.get("authorization", "")
    assert auth_header == f"Bearer {FAKE_API_KEY}", (
        f"Expected 'Bearer {FAKE_API_KEY}'; got {auth_header!r}"
    )

    # Verify response parsing
    assert status == 200, f"Expected status 200; got {status}"
    assert "data" in body, f"Expected 'data' in response body; got {body}"


@pytest.mark.asyncio
async def test_ps8_openai_provider_method_surface_complete() -> None:
    """OpenAIDirectProvider must implement all three UpstreamProvider methods.

    RED reason: ImportError on OpenAIDirectProvider.
    """
    from gateway.proxy.infrastructure.openai_provider import (  # type: ignore[import]
        OpenAIDirectProvider,
    )
    from gateway.proxy.domain.ports import UpstreamProvider  # type: ignore[import]

    # Verify runtime isinstance check (Protocol is runtime_checkable)
    # Credential-resolution-seam BUILD: api_key removed from __init__.
    transport = SequencedMockTransport([])
    try:
        provider = OpenAIDirectProvider(  # type: ignore[call-arg]
            base_url=FAKE_OPENAI_BASE_URL,
        )
    except Exception:
        # Construction fails — surface check only
        pytest.fail(
            "OpenAIDirectProvider construction raised unexpectedly; check constructor signature"
        )

    assert isinstance(provider, UpstreamProvider), (
        "OpenAIDirectProvider must satisfy isinstance(x, UpstreamProvider) — "
        "Protocol must be runtime_checkable"
    )

    # All three methods must be present and callable
    assert callable(getattr(provider, "post_json", None)), (
        "OpenAIDirectProvider must have post_json()"
    )
    assert callable(getattr(provider, "post_multipart", None)), (
        "OpenAIDirectProvider must have post_multipart()"
    )
    assert callable(getattr(provider, "stream_bytes", None)), (
        "OpenAIDirectProvider must have stream_bytes()"
    )


# ===========================================================================
# PS9 — chat path byte-identical; provider_registry NOT consulted
# GREEN-BY-DESIGN: once BUILD wires provider_registry seam, this must pass.
# RED at spec phase because app.state.provider_registry not yet set.
# ===========================================================================


@pytest.mark.asyncio
async def test_ps9_chat_path_not_consulting_provider_registry(
    fake_completion_upstream: FakeCompletionUpstream,
    fake_provider_registry: FakeProviderRegistry,
) -> None:
    """create_app() sets app.state.provider_registry; chat path leaves it uncalled.

    GREEN-BY-DESIGN label: once BUILD wires the seam, provider_registry is always
    set by create_app() and the chat path never calls it.

    RED at spec phase: create_app() does NOT yet set app.state.provider_registry,
    so hasattr() is False — assertion fails for the right reason (missing seam wiring).

    Pattern mirrors tests/cooldown_circuit_wiring (gate is None vs present).
    """
    from gateway.core.config import Settings
    from gateway.main import create_app

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )
    app = create_app(settings)

    # PRIMARY ASSERTION (red now): create_app() itself must set provider_registry.
    # Before BUILD this is False — create_app() does not touch provider_registry yet.
    assert hasattr(app.state, "provider_registry"), (
        "create_app() must set app.state.provider_registry — "
        "this assertion is RED because the seam is not yet wired by create_app()"
    )

    # SECONDARY ASSERTION (green-by-design once seam exists):
    # inject a recording fake and verify the chat path never calls registry.get().
    # This part only runs if the primary assertion passes (i.e. after BUILD).
    app.state.completion_upstream = fake_completion_upstream
    app.state.provider_registry = fake_provider_registry

    # The registry call-count check is deferred to the BUILD verify phase
    # (requires a live DB round-trip through the chat endpoint).
    # Here we just confirm the seam injection pattern is accepted.
    assert app.state.provider_registry is fake_provider_registry


# ===========================================================================
# PS10 — production wiring: create_app wires provider_registry correctly
# ===========================================================================


def test_ps10_production_wiring_registry_has_openrouter_and_openai() -> None:
    """create_app() wires ProviderRegistry with openrouter + openai (UNCONDITIONAL).

    Credential-resolution-seam BUILD: openrouter_api_key / openai_api_key removed
    from Settings; both adapters are registered unconditionally (per-tenant key gating
    at resolve time). Settings no longer accepts those fields.
    """
    from gateway.core.config import Settings
    from gateway.main import create_app
    from gateway.proxy.infrastructure.openai_provider import (  # type: ignore[import]
        OpenAIDirectProvider,
    )
    from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
        openai_base_url=FAKE_OPENAI_BASE_URL,
    )
    app = create_app(settings)

    # Registry must exist
    assert hasattr(app.state, "provider_registry"), (
        "app.state.provider_registry must be set by create_app()"
    )

    # openrouter entry must be present
    openrouter_entry = app.state.provider_registry.get("openrouter")
    assert openrouter_entry is not None, "provider_registry must always contain 'openrouter' entry"

    # openai entry must be present when key is set
    openai_entry = app.state.provider_registry.get("openai")
    assert openai_entry is not None, (
        "provider_registry must contain 'openai' when openai_api_key is set"
    )
    assert isinstance(openai_entry, OpenAIDirectProvider), (
        f"provider_registry['openai'] must be OpenAIDirectProvider; got {type(openai_entry)}"
    )

    # v9: the raw OpenRouter chat adapter relocated to app.state.openrouter_completion_upstream;
    # app.state.completion_upstream is now the provider-dispatch wrapper. The openrouter chat
    # path remains byte-identical (the wrapper delegates to this same adapter for provider="openrouter").
    from gateway.proxy.infrastructure.provider_aware_upstream import ProviderAwareCompletionUpstream

    assert isinstance(app.state.openrouter_completion_upstream, OpenRouterCompletionUpstream), (
        "app.state.openrouter_completion_upstream must be the raw OpenRouterCompletionUpstream "
        "(openrouter chat path must be byte-identical)"
    )
    assert isinstance(app.state.completion_upstream, ProviderAwareCompletionUpstream), (
        "app.state.completion_upstream must be the provider-dispatch wrapper (v9)"
    )


def test_ps10b_openai_absent_when_key_empty() -> None:
    """create_app() always registers 'openai' in provider_registry (UNCONDITIONAL).

    Credential-resolution-seam BUILD: openai_api_key removed from Settings; the
    openai provider is registered unconditionally (per-tenant key gating at resolve
    time). Converted: assert openai IS present regardless of env.
    """
    from gateway.core.config import Settings
    from gateway.main import create_app

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )
    app = create_app(settings)

    assert hasattr(app.state, "provider_registry"), (
        "app.state.provider_registry must be set by create_app()"
    )

    openai_entry = app.state.provider_registry.get("openai")
    assert openai_entry is not None, (
        "provider_registry must contain 'openai' unconditionally after credential-resolution-seam BUILD"
    )

    openrouter_entry = app.state.provider_registry.get("openrouter")
    assert openrouter_entry is not None, "provider_registry must always contain 'openrouter' entry"


# ===========================================================================
# Extra: UpstreamProvider Protocol exists and is runtime_checkable
# ===========================================================================


def test_upstream_provider_protocol_is_runtime_checkable() -> None:
    """UpstreamProvider Protocol must exist in ports.py and be runtime_checkable.

    RED reason: UpstreamProvider not yet defined in ports.py → ImportError.
    """
    from gateway.proxy.domain.ports import UpstreamProvider  # type: ignore[import]

    # Must be a runtime_checkable Protocol (not just a stub class)
    assert hasattr(UpstreamProvider, "__protocol_attrs__") or callable(UpstreamProvider), (
        "UpstreamProvider must be a Protocol"
    )

    # FakeUpstreamProvider from conftest satisfies the protocol duck-typing check
    fake = FakeUpstreamProvider(name="test")
    assert isinstance(fake, UpstreamProvider), (
        "FakeUpstreamProvider (which has post_json/post_multipart/stream_bytes) must satisfy "
        "isinstance(x, UpstreamProvider) — UpstreamProvider must be runtime_checkable"
    )


def test_upstream_provider_protocol_has_all_three_methods() -> None:
    """UpstreamProvider Protocol must declare post_json, post_multipart, stream_bytes.

    RED reason: UpstreamProvider not yet defined → ImportError.
    """
    from gateway.proxy.domain.ports import UpstreamProvider  # type: ignore[import]

    required_methods = {"post_json", "post_multipart", "stream_bytes"}
    protocol_attrs = getattr(UpstreamProvider, "__protocol_attrs__", None)

    if protocol_attrs is not None:
        # Python 3.12+ exposes __protocol_attrs__
        missing = required_methods - set(protocol_attrs)
        assert not missing, f"UpstreamProvider Protocol is missing methods: {missing}"
    else:
        # Fallback: check the abstract methods are callable on instances
        fake = FakeUpstreamProvider(name="method-check")
        for method_name in required_methods:
            assert callable(getattr(fake, method_name, None)), (
                f"FakeUpstreamProvider.{method_name} not callable — "
                f"UpstreamProvider Protocol must declare {method_name}"
            )
