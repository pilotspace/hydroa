"""Red suite for provider-secret-chain-hardening (v22 task 1).

SECURITY regression. Every provider transport-error wrap currently does
``raise UpstreamUnavailableError(str(exc)) from exc`` (or ``from terminal_exc``).
``from exc`` re-attaches the chained httpx error on ``__cause__``; that httpx error
carries ``.request`` whose HEADERS hold the upstream auth secret (api-key / Bearer /
AWS SigV4 Authorization). A crash-reporter walking ``exc.__cause__.request.headers``
can harvest the secret. The fix is ``from None`` at every site — behavior-preserving
(same exception TYPE + MESSAGE), only the ``__cause__`` chain is suppressed.

These tests assert, per adapter (+ the shared retry seam), that a transport error
produces ``UpstreamUnavailableError`` with ``__cause__ is None`` and the secret is
not in the message. RED today because the src still uses ``from exc`` so ``__cause__``
is the live httpx error (whose ``.request`` carries the secret), not None.

CONTRACT (FROZEN @ v1): provider-secret-chain-hardening TASK.md §3.
  - 13 sites flip ``from exc``/``from terminal_exc`` → ``from None``.
  - One covering test per site (shared seam ×3 + per-adapter ×10).

HTTP via httpx.MockTransport; no network. asyncio_mode=auto (no decorator needed).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest

from gateway.proxy.domain.credential_context import reset_provider_credential, set_provider_credential
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BedrockCredential, BearerCredential, AzureCredential
from gateway.proxy.infrastructure.anthropic_upstream import AnthropicCompletionUpstream
from gateway.proxy.infrastructure.azure_config import AzureConfig
from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream
from gateway.proxy.infrastructure.bedrock_embeddings import BedrockEmbeddingsProvider
from gateway.proxy.infrastructure.bedrock_sigv4 import AwsCredentials
from gateway.proxy.infrastructure.bedrock_upstream import BedrockCompletionUpstream
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.gemini_upstream import (
    GeminiCompletionUpstream,
    GoogleEmbeddingsProvider,
)
from gateway.proxy.infrastructure.openai_provider import OpenAIDirectProvider
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream
from gateway.proxy.infrastructure.upstream_retry import execute_with_retry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A transport message that does NOT contain any of the secrets below, so the
# "secret not in str(exc)" assertion is a real guard (not vacuously satisfied by
# an unrelated message). The LEAK vector is __cause__.request.headers, not the text.
_TRANSPORT_MSG = "connection refused by peer"

_CHAT_PAYLOAD: dict[str, Any] = {
    "model": "PLACEHOLDER",
    "messages": [{"role": "user", "content": "hi"}],
}


def _connect_error_handler(request: httpx.Request) -> httpx.Response:
    # httpx attaches `request` to the raised error → its headers carry the secret.
    raise httpx.ConnectError(_TRANSPORT_MSG)


def _mock_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(_connect_error_handler))


async def _drain_stream(gen: AsyncIterator[bytes]) -> None:
    async for _ in gen:
        pass


def _chat_payload(model: str) -> dict[str, Any]:
    return {**_CHAT_PAYLOAD, "model": model}


# ===========================================================================
# Shared retry seam — execute_with_retry (upstream_retry.py:159,183,196)
# ===========================================================================


async def _run_seam(
    do_request: Callable[[], Awaitable[httpx.Response]],
    *,
    max_retries: int = 0,
    deadline_s: float = 0.0,
    backoff_base: float = 0.0,
) -> None:
    def _render(resp: httpx.Response) -> tuple[int, dict[str, Any]]:
        return resp.status_code, resp.json()

    await execute_with_retry(
        do_request,
        _render,
        breaker=CircuitBreaker(),
        provider="seamtest",
        max_retries=max_retries,
        backoff_base=backoff_base,
        deadline_s=deadline_s,
    )


async def test_retry_seam_terminal_timeout_no_cause() -> None:
    # ReadTimeout → upstream_retry.py:159 (terminal path, `from exc`).
    async def do_request() -> httpx.Response:
        raise httpx.ReadTimeout(_TRANSPORT_MSG)

    with pytest.raises(UpstreamUnavailableError) as exc:
        await _run_seam(do_request)
    assert exc.value.__cause__ is None


async def test_retry_seam_connect_error_no_cause() -> None:
    # ConnectError, max_retries=0 → exhausted → upstream_retry.py:183 (`from terminal_exc`).
    async def do_request() -> httpx.Response:
        raise httpx.ConnectError(_TRANSPORT_MSG)

    with pytest.raises(UpstreamUnavailableError) as exc:
        await _run_seam(do_request, max_retries=0)
    assert exc.value.__cause__ is None


async def test_retry_seam_deadline_exceeded_no_cause() -> None:
    # ConnectError, retry allowed but deadline tiny → upstream_retry.py:196
    # (deadline-exceeded path, `from terminal_exc`). backoff_base large so the
    # computed delay overruns the 0.0001s deadline deterministically.
    async def do_request() -> httpx.Response:
        raise httpx.ConnectError(_TRANSPORT_MSG)

    with pytest.raises(UpstreamUnavailableError) as exc:
        await _run_seam(do_request, max_retries=3, deadline_s=0.0001, backoff_base=5.0)
    assert exc.value.__cause__ is None


# ===========================================================================
# OpenRouter — stream() (openrouter_upstream.py:151)
# ===========================================================================


async def test_openrouter_stream_no_cause() -> None:
    # Credential-resolution-seam BUILD: secret supplied via contextvar, not constructor.
    secret = "sk-or-secret-FAKE"
    cred = BearerCredential(secret=secret)
    adapter = OpenRouterCompletionUpstream()
    adapter._client = _mock_client()  # type: ignore[attr-defined]
    token = set_provider_credential(cred)
    try:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await _drain_stream(adapter.stream(_chat_payload("openai/gpt-4o")))
    finally:
        reset_provider_credential(token)
    assert exc.value.__cause__ is None
    assert secret not in str(exc.value)


# ===========================================================================
# OpenAIDirectProvider — post_json / post_multipart / stream_bytes
# (openai_provider.py:97,128,175)
# ===========================================================================


def _openai_adapter() -> OpenAIDirectProvider:
    # Credential-resolution-seam BUILD: api_key removed from __init__.
    adapter = OpenAIDirectProvider()
    adapter._client = _mock_client()  # type: ignore[attr-defined]
    return adapter


async def test_openai_post_json_no_cause() -> None:
    secret = "sk-openai-postjson-FAKE"
    cred = BearerCredential(secret=secret)
    adapter = _openai_adapter()
    token = set_provider_credential(cred)
    try:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await adapter.post_json("/embeddings", {"model": "text-embedding-3-small", "input": "x"})
    finally:
        reset_provider_credential(token)
    assert exc.value.__cause__ is None
    assert secret not in str(exc.value)


async def test_openai_post_multipart_no_cause() -> None:
    secret = "sk-openai-multipart-FAKE"
    cred = BearerCredential(secret=secret)
    adapter = _openai_adapter()
    token = set_provider_credential(cred)
    try:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await adapter.post_multipart(
                "/audio/transcriptions",
                {"file": ("a.mp3", b"bytes", "audio/mpeg")},
                {"model": "whisper-1"},
            )
    finally:
        reset_provider_credential(token)
    assert exc.value.__cause__ is None
    assert secret not in str(exc.value)


async def test_openai_stream_bytes_no_cause() -> None:
    secret = "sk-openai-streambytes-FAKE"
    cred = BearerCredential(secret=secret)
    adapter = _openai_adapter()
    token = set_provider_credential(cred)
    try:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await _drain_stream(adapter.stream_bytes("/audio/speech", {"model": "tts-1", "input": "x"}))
    finally:
        reset_provider_credential(token)
    assert exc.value.__cause__ is None
    assert secret not in str(exc.value)


# ===========================================================================
# Anthropic — stream() (anthropic_upstream.py:658)
# ===========================================================================


async def test_anthropic_stream_no_cause() -> None:
    # Credential-resolution-seam BUILD: api_key removed; secret via contextvar.
    secret = "sk-ant-secret-FAKE"
    cred = BearerCredential(secret=secret)
    adapter = AnthropicCompletionUpstream()
    adapter._client = _mock_client()  # type: ignore[attr-defined]
    token = set_provider_credential(cred)
    try:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await _drain_stream(adapter.stream(_chat_payload("claude-3-5-sonnet-20241022")))
    finally:
        reset_provider_credential(token)
    assert exc.value.__cause__ is None
    assert secret not in str(exc.value)


# ===========================================================================
# Gemini — stream() (gemini_upstream.py:629) + GoogleEmbeddings post_json (753)
# ===========================================================================


async def test_gemini_stream_no_cause() -> None:
    # Credential-resolution-seam BUILD: api_key removed; secret via contextvar.
    secret = "gemini-stream-secret-FAKE"
    cred = BearerCredential(secret=secret)
    adapter = GeminiCompletionUpstream()
    adapter._client = _mock_client()  # type: ignore[attr-defined]
    token = set_provider_credential(cred)
    try:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await _drain_stream(adapter.stream(_chat_payload("gemini-1.5-flash")))
    finally:
        reset_provider_credential(token)
    assert exc.value.__cause__ is None
    assert secret not in str(exc.value)


async def test_gemini_embeddings_no_cause() -> None:
    # Credential-resolution-seam BUILD: api_key removed; secret via contextvar.
    secret = "gemini-embed-secret-FAKE"
    cred = BearerCredential(secret=secret)
    adapter = GoogleEmbeddingsProvider()
    adapter._client = _mock_client()  # type: ignore[attr-defined]
    token = set_provider_credential(cred)
    try:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await adapter.post_json("/embeddings", {"model": "text-embedding-004", "input": "x"})
    finally:
        reset_provider_credential(token)
    assert exc.value.__cause__ is None
    assert secret not in str(exc.value)


# ===========================================================================
# Bedrock — stream() (bedrock_upstream.py:574) + embeddings post_json (193)
# ===========================================================================

_AWS_CREDS = AwsCredentials(
    access_key_id="AKIDTEST000000000000",
    secret_access_key="bedrock-secret-access-key-FAKE",
    region="us-east-1",
)
_BEDROCK_ENDPOINT = "https://bedrock-runtime.us-east-1.amazonaws.com"

# v25 task-3: BedrockCredential travels via contextvar, not ctor args.
_BEDROCK_CRED = BedrockCredential(
    access_key_id="AKIDTEST000000000000",
    secret_access_key="bedrock-secret-access-key-FAKE",
    region="us-east-1",
)


async def test_bedrock_stream_no_cause() -> None:
    # v25 task-3: no ctor credentials=/region=; credential via contextvar.
    # RIGHT-REASON RED: existing ctor still requires them → TypeError until BUILD.
    adapter = BedrockCompletionUpstream(  # type: ignore[call-arg]
        endpoint_url=_BEDROCK_ENDPOINT
    )
    adapter._client = _mock_client()  # type: ignore[attr-defined]
    tok = set_provider_credential(_BEDROCK_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await _drain_stream(
                adapter.stream(_chat_payload("anthropic.claude-3-5-sonnet-20241022-v2:0"))
            )
    finally:
        reset_provider_credential(tok)
    assert exc.value.__cause__ is None
    assert _BEDROCK_CRED.secret_access_key.get_secret_value() not in str(exc.value)


async def test_bedrock_embeddings_no_cause() -> None:
    # v25 task-3: no ctor credentials=/region=; credential via contextvar.
    # RIGHT-REASON RED: existing ctor still requires them → TypeError until BUILD.
    adapter = BedrockEmbeddingsProvider(  # type: ignore[call-arg]
        endpoint_url=_BEDROCK_ENDPOINT
    )
    adapter._client = _mock_client()  # type: ignore[attr-defined]
    tok = set_provider_credential(_BEDROCK_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await adapter.post_json(
                "/embeddings", {"model": "amazon.titan-embed-text-v2:0", "input": "x"}
            )
    finally:
        reset_provider_credential(tok)
    assert exc.value.__cause__ is None
    assert _BEDROCK_CRED.secret_access_key.get_secret_value() not in str(exc.value)


# ===========================================================================
# Azure — stream() (azure_upstream.py:160)
# ===========================================================================


async def test_azure_stream_no_cause() -> None:
    secret = "azure-stream-secret-FAKE"
    # v25 task-3: AzureCompletionUpstream drops config= ctor arg; credentials via contextvar.
    # RIGHT-REASON RED: existing ctor still requires config= → TypeError until BUILD.
    adapter = AzureCompletionUpstream(  # type: ignore[call-arg]
        token_provider_cache=None,
    )
    adapter._client = _mock_client()  # type: ignore[attr-defined]
    cred = AzureCredential(
        mode="api_key",
        endpoint="https://r.openai.azure.com",
        api_version="2024-10-21",
        deployment_map={"gpt-4o": "prod-chat"},
        api_key=secret,
    )
    tok = set_provider_credential(cred)
    try:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await _drain_stream(adapter.stream(_chat_payload("gpt-4o")))
    finally:
        reset_provider_credential(tok)
    assert exc.value.__cause__ is None
    assert secret not in str(exc.value)
