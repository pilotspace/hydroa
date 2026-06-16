"""credential-resolution-seam §3 (v2 amendment) — the SHARED credential gate.

These tests pin ``resolve_provider_credential`` — the single source of truth that BOTH the
chat use-case (``CompletionUseCase._resolve_credential``) and the non-chat use-cases
(embeddings / images / audio) delegate to. Covering it directly proves, for every modality
at once:

  * the STAGED gate: only ``BYOK_BEARER_PROVIDERS`` (openrouter/openai/anthropic/google) are
    resolved; Bedrock/Azure are SKIPPED (env-bound until task 3) — never a 402 for a
    still-env-bound provider (regression guard for the v1 build's ungated resolve, which
    would have 402'd Bedrock/Azure);
  * fail-closed ``ProviderKeyMissing`` → ``ProblemError(402, ERR_PROVIDER_KEY_MISSING)``;
  * the resolved credential is placed in the request-scoped contextvar.

End-to-end wiring for each non-chat modality is covered by the embeddings_endpoint /
images_endpoint / audio_endpoints suites (which now resolve via the stub resolver).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from gateway.core.errors import ProblemError
from gateway.proxy.application.use_cases import resolve_provider_credential
from gateway.proxy.domain.credential_context import (
    get_provider_credential,
    reset_provider_credential,
)
from gateway.proxy.domain.provider_credentials import BearerCredential, ProviderKeyMissing


class _RecordingResolver:
    """Fake TenantCredentialResolver that records calls and returns a fixed Bearer cred."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str]] = []

    async def resolve(self, tenant_id: Any, provider: str) -> BearerCredential:
        self.calls.append((tenant_id, provider))
        return BearerCredential(secret=f"secret-for-{provider}")


class _MissingResolver:
    """Fake resolver that always fails closed with ProviderKeyMissing."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str]] = []

    async def resolve(self, tenant_id: Any, provider: str) -> BearerCredential:
        self.calls.append((tenant_id, provider))
        raise ProviderKeyMissing(provider)


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()


async def test_none_resolver_returns_none_and_sets_nothing(tenant_id: uuid.UUID) -> None:
    """No resolver wired (legacy/test) → no token, contextvar stays unset."""
    token = await resolve_provider_credential(None, tenant_id, "openrouter")
    assert token is None
    assert get_provider_credential() is None


@pytest.mark.parametrize("provider", ["openrouter", "openai", "anthropic", "google"])
async def test_bearer_provider_resolves_and_sets_contextvar(
    tenant_id: uuid.UUID, provider: str
) -> None:
    """A converted Bearer provider is resolved and placed in the contextvar."""
    resolver = _RecordingResolver()
    token = await resolve_provider_credential(resolver, tenant_id, provider)
    assert token is not None, f"{provider} must resolve to a token"
    try:
        assert resolver.calls == [(tenant_id, provider)]
        cred = get_provider_credential()
        assert isinstance(cred, BearerCredential)
        assert cred.secret.get_secret_value() == f"secret-for-{provider}"
    finally:
        reset_provider_credential(token)  # type: ignore[arg-type]
    assert get_provider_credential() is None, "contextvar must reset cleanly"


@pytest.mark.parametrize("provider", ["bedrock", "azure"])
async def test_staged_provider_skips_resolution(
    tenant_id: uuid.UUID, provider: str
) -> None:
    """Bedrock/Azure are env-bound until task 3 — resolution is SKIPPED, NEVER consulted.

    Regression guard: the v1 build resolved every provider unconditionally, which would
    have raised ProviderKeyMissing → 402 for a Bedrock/Azure request that has no BYOK row.
    """
    resolver = _RecordingResolver()
    token = await resolve_provider_credential(resolver, tenant_id, provider)
    assert token is None, f"{provider} must SKIP resolution (staged, env-bound)"
    assert resolver.calls == [], f"resolver must NOT be consulted for staged provider {provider}"
    assert get_provider_credential() is None


async def test_missing_bearer_key_maps_to_402(tenant_id: uuid.UUID) -> None:
    """A converted provider with no enabled key → ProblemError(402, ERR_PROVIDER_KEY_MISSING).

    Fail-closed: the contextvar is never set when resolution fails.
    """
    resolver = _MissingResolver()
    with pytest.raises(ProblemError) as exc_info:
        await resolve_provider_credential(resolver, tenant_id, "openrouter")
    assert exc_info.value.status == 402
    assert exc_info.value.code == "ERR_PROVIDER_KEY_MISSING"
    assert resolver.calls == [(tenant_id, "openrouter")]
    assert get_provider_credential() is None, "no credential set on the fail-closed path"


async def test_missing_key_error_carries_no_secret(tenant_id: uuid.UUID) -> None:
    """The 402 chain carries no secret/ciphertext and is severed (`from None`)."""
    resolver = _MissingResolver()
    with pytest.raises(ProblemError) as exc_info:
        await resolve_provider_credential(resolver, tenant_id, "openai")
    # raised `from None` — no chained ProviderKeyMissing leaking provider internals
    assert exc_info.value.__cause__ is None
    assert "secret" not in str(exc_info.value).lower()
