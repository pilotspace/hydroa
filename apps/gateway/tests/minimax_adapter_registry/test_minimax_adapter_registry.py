"""Failing-first (RED) suite for minimax-adapter-registry (TASK.md §4, contract FROZEN @ v1).

One test per §2 SCENARIOS. Every test targets code that does not exist yet ("minimax" is
absent from every provider collection until BUILD), so each fails NOW for the RIGHT reason:
AssertionError / KeyError / ImportError-free-but-missing-membership — never a broken harness.

Run only this suite:
  cd apps/gateway && uv run pytest tests/minimax_adapter_registry/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from gateway.proxy.domain.provider_credentials import (
    BYOK_PROVIDERS,
    BearerCredential,
    PROVIDER_VALUE_SET,
    ProviderKeyMissing,
    ProviderName,
)
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.openai_provider import OpenAIDirectProvider
from gateway.proxy.infrastructure.provider_registry import select_provider
from gateway.proxy.infrastructure.tenant_provider_key_store import _parts_to_credential

from .conftest import (
    PROVIDER_KEYS,
    assert_problem,
    auth,
    bootstrap_fresh_db,
    client_for,
    make_app,
    make_settings,
    signup_tenant,
    store_of,
    tenant_uuid,
)

_ALL_SEVEN = frozenset(
    {"openrouter", "openai", "anthropic", "google", "bedrock", "azure", "minimax"}
)
_MINIMAX_SECRET = "sk-cp-test-minimax-secret-never-return-me"  # noqa: S105


# ===========================================================================
# Scenario: minimax joins the provider value set
# ===========================================================================


def test_minimax_joins_the_provider_value_set() -> None:
    assert PROVIDER_VALUE_SET == _ALL_SEVEN, (
        f"PROVIDER_VALUE_SET must equal {_ALL_SEVEN!r}, got {PROVIDER_VALUE_SET!r}"
    )
    assert BYOK_PROVIDERS == _ALL_SEVEN, (
        f"BYOK_PROVIDERS must equal {_ALL_SEVEN!r}, got {BYOK_PROVIDERS!r}"
    )
    # ProviderName is a Literal — exercised structurally via a real value assignment.
    minimax_literal: ProviderName = "minimax"  # type: ignore[assignment]
    assert minimax_literal == "minimax"


# ===========================================================================
# Scenario: minimax chat adapter is registered unconditionally at boot
# ===========================================================================


def test_minimax_chat_adapter_registered_unconditionally(app_no_db: Any) -> None:
    chat_adapters: dict[str, Any] = app_no_db.state.chat_adapters
    assert "minimax" in chat_adapters, (
        "chat_adapters['minimax'] must be registered unconditionally (BYOK-only, no boot env "
        "check) — it is absent, this is the pre-BUILD (RED) state."
    )
    minimax_adapter = chat_adapters["minimax"]
    assert isinstance(minimax_adapter, OpenAIDirectProvider)
    assert minimax_adapter._client.base_url == httpx.URL("https://api.minimax.io/v1/"), (
        f"minimax adapter base_url must be https://api.minimax.io/v1, "
        f"got {minimax_adapter._client.base_url}"
    )
    # The other 6 providers must be present and unaffected.
    for provider in ("openrouter", "openai", "anthropic", "google", "bedrock", "azure"):
        assert provider in chat_adapters, f"pre-existing provider '{provider}' must be unaffected"


# ===========================================================================
# Scenario: minimax is chat-only — no non-chat registration
# ===========================================================================


def test_minimax_not_registered_for_non_chat_modalities(app_no_db: Any) -> None:
    registry = app_no_db.state.provider_registry
    assert registry.get("minimax") is None, (
        "minimax must NOT appear in the non-chat provider registry (mirrors 'anthropic', which "
        "also has no non-chat entry) — embeddings/image/audio are out of scope this milestone."
    )
    # Non-chat entries for pre-existing providers stay unaffected.
    for provider in ("openrouter", "openai", "google", "bedrock", "azure"):
        assert registry.get(provider) is not None, f"non-chat '{provider}' must be unaffected"


# ===========================================================================
# Scenario: OpenAIDirectProvider generalizes without changing openai's behavior
# ===========================================================================


async def test_openai_direct_provider_default_provider_name_unchanged() -> None:
    """A default-constructed (__new__) instance still fails closed as 'openai'."""
    provider = OpenAIDirectProvider.__new__(OpenAIDirectProvider)
    provider._client = httpx.AsyncClient(base_url="https://api.openai.com/v1")  # type: ignore[attr-defined]
    provider._breaker = CircuitBreaker()  # type: ignore[attr-defined]
    provider._metrics_registry = None  # type: ignore[attr-defined]
    with pytest.raises(ProviderKeyMissing) as exc_info:
        await provider.complete({"model": "gpt-4o", "messages": []})
    assert exc_info.value.code == "ERR_PROVIDER_KEY_MISSING"
    assert "openai" in str(exc_info.value), (
        f"default provider_name must still label as 'openai', got: {exc_info.value}"
    )


async def test_openai_direct_provider_custom_provider_name_minimax() -> None:
    """An instance constructed with provider_name='minimax' fails closed as 'minimax'."""
    provider = OpenAIDirectProvider.__new__(OpenAIDirectProvider)
    provider._client = httpx.AsyncClient(base_url="https://api.minimax.io/v1")  # type: ignore[attr-defined]
    provider._breaker = CircuitBreaker()  # type: ignore[attr-defined]
    provider._metrics_registry = None  # type: ignore[attr-defined]
    provider._provider_name = "minimax"  # type: ignore[attr-defined]
    with pytest.raises(ProviderKeyMissing) as exc_info:
        await provider.complete({"model": "MiniMax-M3", "messages": []})
    assert exc_info.value.code == "ERR_PROVIDER_KEY_MISSING"
    assert "minimax" in str(exc_info.value), (
        f"provider_name='minimax' must label the failure as 'minimax', got: {exc_info.value}"
    )


# ===========================================================================
# Scenario: minimax's Bearer credential reconstructs correctly
# ===========================================================================


def test_parts_to_credential_minimax_returns_bearer() -> None:
    cred = _parts_to_credential("minimax", _MINIMAX_SECRET, None, None)
    assert isinstance(cred, BearerCredential)
    assert cred.secret.get_secret_value() == _MINIMAX_SECRET


# ===========================================================================
# Scenario: select_provider resolves "minimax" once wired
# ===========================================================================


def test_select_provider_resolves_minimax(app_no_db: Any) -> None:
    registry = app_no_db.state.provider_registry
    # minimax is chat-only — exercised through the chat adapter map's ProviderRegistry-shaped
    # wrapper is out of reach here; instead prove select_provider's generic dict-lookup path
    # against a registry that DOES carry a minimax entry (constructed the same way the wiring
    # block would, if minimax were also chat+non-chat) — pins the lookup semantics only.
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry

    minimax_adapter = app_no_db.state.chat_adapters.get("minimax")
    assert minimax_adapter is not None, "minimax chat adapter must exist to be selectable"
    test_registry = ProviderRegistry({"minimax": minimax_adapter})
    resolved = select_provider("chat", "minimax", test_registry)
    assert resolved is minimax_adapter


# ===========================================================================
# Scenario: the admin BYOK allow-list accepts minimax as a Bearer-shaped provider
# ===========================================================================


def test_bearer_providers_admin_allowlist_includes_minimax() -> None:
    from gateway.proxy.api.provider_keys_admin_router import _BEARER_PROVIDERS

    assert "minimax" in _BEARER_PROVIDERS, (
        "_BEARER_PROVIDERS must include 'minimax' so PUT /admin/provider-keys/minimax "
        "builds a BearerCredential via the existing single-secret dispatch."
    )


# ===========================================================================
# Scenario: a tenant stores a MiniMax key via the existing admin API
# ===========================================================================


async def test_admin_put_minimax_key_roundtrip() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(
            client, tenant_name="MiniMaxRT", email="owner-minimax-rt@byok.io"
        )
        resp = await client.put(
            f"{PROVIDER_KEYS}/minimax", headers=auth(token), json={"secret": _MINIMAX_SECRET}
        )
        assert resp.status_code == 200, (
            f"PUT minimax expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["provider"] == "minimax"
        assert body["configured"] is True
        assert _MINIMAX_SECRET not in resp.text, "SECURITY: minimax secret leaked in PUT response"

        get_resp = await client.get(f"{PROVIDER_KEYS}/minimax", headers=auth(token))
        assert get_resp.status_code == 200
        assert get_resp.json()["configured"] is True

        cred = await store_of(app).get(tenant_uuid(tenant_id), "minimax")
        assert isinstance(cred, BearerCredential)
        assert cred.secret.get_secret_value() == _MINIMAX_SECRET

    await engine.dispose()


# ===========================================================================
# Reject: PUT minimax key with an empty secret is rejected
# ===========================================================================


async def test_admin_put_minimax_empty_secret_rejected() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(
            client, tenant_name="MiniMaxEmpty", email="owner-minimax-empty@byok.io"
        )
        resp = await client.put(
            f"{PROVIDER_KEYS}/minimax", headers=auth(token), json={"secret": ""}
        )
        assert_problem(resp, 422, "ERR_PROVIDER_CREDENTIAL_INCOMPLETE")

        # Nothing persisted — prior (absent) state unchanged.
        cred = await store_of(app).get(tenant_uuid(tenant_id), "minimax")
        assert cred is None

    await engine.dispose()


# ===========================================================================
# Reject: PUT to a still-unknown provider is rejected (unaffected by this widening)
# ===========================================================================


async def test_admin_put_unknown_provider_still_rejected() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="UnknownProvider", email="owner-unknown@byok.io"
        )
        resp = await client.put(
            f"{PROVIDER_KEYS}/not-a-real-provider", headers=auth(token), json={"secret": "x"}
        )
        assert_problem(resp, 422, "ERR_PROVIDER_UNKNOWN")
        assert PROVIDER_VALUE_SET == _ALL_SEVEN, "widening must not have leaked an 8th provider"

    await engine.dispose()


# ===========================================================================
# Scenario: re-PUT (upsert) replaces an existing minimax key
# ===========================================================================


async def test_admin_put_minimax_key_upsert_replaces() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(
            client, tenant_name="MiniMaxUpsert", email="owner-minimax-upsert@byok.io"
        )
        await client.put(
            f"{PROVIDER_KEYS}/minimax", headers=auth(token), json={"secret": "first-key"}
        )
        resp = await client.put(
            f"{PROVIDER_KEYS}/minimax", headers=auth(token), json={"secret": "second-key"}
        )
        assert resp.status_code == 200
        assert resp.json()["configured"] is True

        cred = await store_of(app).get(tenant_uuid(tenant_id), "minimax")
        assert isinstance(cred, BearerCredential)
        assert cred.secret.get_secret_value() == "second-key", "upsert must replace, not append"

    await engine.dispose()


# ===========================================================================
# Scenario: DELETE removes a configured minimax key
# ===========================================================================


async def test_admin_delete_minimax_key() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(
            client, tenant_name="MiniMaxDelete", email="owner-minimax-delete@byok.io"
        )
        await client.put(
            f"{PROVIDER_KEYS}/minimax", headers=auth(token), json={"secret": _MINIMAX_SECRET}
        )

        del_resp = await client.delete(f"{PROVIDER_KEYS}/minimax", headers=auth(token))
        assert del_resp.status_code == 204

        get_resp = await client.get(f"{PROVIDER_KEYS}/minimax", headers=auth(token))
        assert_problem(get_resp, 404, "ERR_PROVIDER_KEY_NOT_FOUND")

        cred = await store_of(app).get(tenant_uuid(tenant_id), "minimax")
        assert cred is None

    await engine.dispose()
