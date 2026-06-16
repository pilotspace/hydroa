"""Red suite for azure-verify (v21 task 6) — frozen contract.

EARNED-GREEN, no-docker integration verification of the v21 Azure OpenAI provider
surface (chat · streaming · embeddings · api-key + AAD auth · content-filter→fallback)
against an INDEPENDENT stub over real TCP (127.0.0.1). The stub does NOT import gateway
code; the AAD path is a genuine end-to-end oracle because the stub MINTS the token its own
token endpoint, and the chat/embed routes ACCEPT only a Bearer equal to that minted token —
so a green AAD test proves the REAL AzureADTokenProvider fetched + presented it correctly.

The stub lives at the repo-root ``scripts/v21_azure_stub.py`` (pure stdlib, self-contained)
and is loaded here via importlib so the single operator-runnable artifact is also the test
harness.

RED until BUILD creates ``scripts/v21_azure_stub.py`` (importlib load raises FileNotFoundError)
and the live script + overlay (AV9 existence asserts).

v25 task-3 amendment: _chat_upstream() drops config= and token_provider= ctor args; gains
token_provider_cache=. Credentials travel via AzureCredential in the contextvar. Each adapter
call is wrapped with set_provider_credential / reset_provider_credential.
AzureEmbeddingsProvider likewise drops config= ctor arg.
"""

from __future__ import annotations

import importlib.util
import json
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.provider_credentials import AzureCredential
from gateway.proxy.infrastructure.azure_ad import AzureADConfig, AzureADTokenProvider
from gateway.proxy.infrastructure.azure_config import AzureConfig
from gateway.proxy.infrastructure.azure_embeddings import AzureEmbeddingsProvider
from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Load the independent stub from the repo root (pure stdlib, no gateway import).
# RED: spec_from_file_location's loader raises FileNotFoundError until BUILD
# writes scripts/v21_azure_stub.py.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]  # azure_verify→tests→gateway→apps→<root>
_STUB_PATH = _REPO_ROOT / "scripts" / "v21_azure_stub.py"


def _load_stub() -> Any:
    spec = importlib.util.spec_from_file_location("v21_azure_stub", _STUB_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # RED here: FileNotFoundError until the stub exists
    return mod


stub = _load_stub()

# ---------------------------------------------------------------------------
# Fake public test values (NEVER real Azure creds). These MUST equal the stub's
# EXPECTED_API_KEY / EXPECTED_CLIENT_SECRET / MINTED_TOKEN constants.
# ---------------------------------------------------------------------------

_API_KEY = stub.EXPECTED_API_KEY
_CLIENT_SECRET = stub.EXPECTED_CLIENT_SECRET
_MINTED = stub.MINTED_TOKEN
_API_VERSION = "2024-10-21"
_TENANT = "tenant-1"

# model → Azure deployment (the deployment suffix keys the stub's behavior).
_DEPLOY_MAP = {
    "az-chat": "deploy-chat",
    "az-stream": "deploy-stream",
    "az-retry": "deploy-retry-once",
    "az-cf": "deploy-content-filter",
    "az-ok": "deploy-ok",
    "az-embed": "deploy-embed",
}


def _az_cfg(base_url: str) -> AzureConfig:
    return AzureConfig(
        api_key=_API_KEY,
        endpoint=base_url,
        api_version=_API_VERSION,
        deployment_map=dict(_DEPLOY_MAP),
    )


def _az_cred(base_url: str) -> AzureCredential:
    """v25 task-3: AzureCredential (api_key mode) for contextvar-based tests."""
    return AzureCredential(
        mode="api_key",
        endpoint=base_url,
        api_version=_API_VERSION,
        deployment_map=dict(_DEPLOY_MAP),
        api_key=_API_KEY,
    )


def _az_aad_cred(base_url: str) -> AzureCredential:
    """v25 task-3: AzureCredential (aad mode) for contextvar-based AAD tests.

    ``authority`` is pinned to the stub's base URL so the AAD token mint hits the
    local stub (production defaults to ``https://login.microsoftonline.com``; the
    credential never derives the authority from the resource endpoint).
    """
    return AzureCredential(
        mode="aad",
        endpoint=base_url,
        api_version=_API_VERSION,
        deployment_map=dict(_DEPLOY_MAP),
        tenant_id=_TENANT,
        client_id="c",
        client_secret=_CLIENT_SECRET,
        authority=base_url,
    )


# ---------------------------------------------------------------------------
# Stub server fixture — session-scoped on an ephemeral 127.0.0.1 port; reset
# (counters) before each test for double-pass-style idempotency.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def stub_server() -> Any:
    server: HTTPServer = stub.make_stub_server(port=0)  # 0 → OS-assigned ephemeral port
    thread = stub.start_stub_in_thread(server)
    host, port = server.server_address[0], server.server_address[1]
    base_url = f"http://{host}:{port}"
    try:
        yield {"server": server, "base_url": base_url, "host": host, "port": port}
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _reset_stub(stub_server: dict[str, Any]) -> None:
    httpx.post(f"{stub_server['base_url']}/__reset", timeout=5)


def _chat_upstream(
    base_url: str, *, max_retries: int = 0
) -> AzureCompletionUpstream:
    """v25 task-3: no ctor config= or token_provider=; credentials travel via contextvar.

    RIGHT-REASON RED: existing ctor still requires config= → TypeError until BUILD.
    """
    return AzureCompletionUpstream(  # type: ignore[call-arg]
        token_provider_cache=None,
        max_retries=max_retries,
        backoff_base=0.0,  # no real sleep between retries — fast test
        retry_deadline_s=0.0,
    )


# ---------------------------------------------------------------------------
# AV1 — the independent oracle rejects wrong / missing auth (not a rubber stamp).
# ---------------------------------------------------------------------------


async def test_AV1_independent_oracle_rejects_wrong_and_missing_auth(
    stub_server: dict[str, Any],
) -> None:
    base = stub_server["base_url"]
    chat_url = f"{base}/openai/deployments/deploy-chat/chat/completions?api-version={_API_VERSION}"
    body = json.dumps({"model": "az-chat", "messages": []}).encode()
    async with httpx.AsyncClient(timeout=10) as client:
        # wrong api-key → 401
        r_bad = await client.post(
            chat_url, content=body, headers={"api-key": "WRONG", "content-type": "application/json"}
        )
        assert r_bad.status_code == 401, r_bad.text
        # no auth at all → 401
        r_none = await client.post(
            chat_url, content=body, headers={"content-type": "application/json"}
        )
        assert r_none.status_code == 401, r_none.text
        # token endpoint with the wrong client_secret → 401
        r_tok = await client.post(
            f"{base}/{_TENANT}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "c",
                "client_secret": "WRONG-SECRET",
                "scope": "https://cognitiveservices.azure.com/.default",
            },
        )
        assert r_tok.status_code == 401, r_tok.text


# ---------------------------------------------------------------------------
# AV2 — real api-key chat ACCEPTED, routed by deployment, OpenAI shape + billing.
# ---------------------------------------------------------------------------


async def test_AV2_real_api_key_chat_accepted_routed_by_deployment(
    stub_server: dict[str, Any],
) -> None:
    upstream = _chat_upstream(stub_server["base_url"])
    tok = set_provider_credential(_az_cred(stub_server["base_url"]))
    try:
        status, body = await upstream.complete(
            {"model": "az-chat", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200, body  # api-key ACCEPTED + api-version present (stub 400s without it)
    assert body["object"] == "chat.completion"
    usage = body["usage"]
    assert isinstance(usage["prompt_tokens"], int)
    assert isinstance(usage["completion_tokens"], int)
    assert isinstance(usage["total_tokens"], int)
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    # the request hit the RESOLVED deployment path (proves resolve_deployment + build_url).
    async with httpx.AsyncClient(timeout=10) as client:
        counters = (await client.get(f"{stub_server['base_url']}/__counters")).json()
    assert counters.get("deploy-chat", 0) >= 1


# ---------------------------------------------------------------------------
# AV3 — AAD minted-token vector accepted end-to-end (the real token provider).
# ---------------------------------------------------------------------------


async def test_AV3_aad_minted_token_accepted_end_to_end(stub_server: dict[str, Any]) -> None:
    base = stub_server["base_url"]
    # v25 task-3: adapter reads AzureCredential (aad mode) from contextvar; it calls
    # token_provider_cache.get_or_create(cred.to_azure_ad_config()) to get the provider.
    # For the verify test we pass token_provider_cache=None (no real cache needed for the
    # oracle — the AzureADTokenProvider is exercised via the real contextvar path).
    upstream = _chat_upstream(base)
    tok = set_provider_credential(_az_aad_cred(base))
    try:
        status, body = await upstream.complete(
            {"model": "az-chat", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    # 200 proves: the adapter fetched the stub-minted token and presented it as Bearer,
    # and the stub ACCEPTED Bearer == its own minted token. End-to-end AAD oracle.
    assert status == 200, body
    assert body["object"] == "chat.completion"


# ---------------------------------------------------------------------------
# AV4 — real streaming ACCEPTED; OpenAI SSE chunks end with a usage frame + [DONE].
# ---------------------------------------------------------------------------


async def test_AV4_real_streaming_accepted_with_usage(stub_server: dict[str, Any]) -> None:
    upstream = _chat_upstream(stub_server["base_url"])
    chunks: list[bytes] = []
    tok = set_provider_credential(_az_cred(stub_server["base_url"]))
    try:
        async for chunk in upstream.stream(
            {"model": "az-stream", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        ):
            chunks.append(chunk)
    finally:
        reset_provider_credential(tok)
    text = b"".join(chunks).decode("utf-8")
    assert "chat.completion.chunk" in text  # stub ACCEPTED + emitted real OpenAI SSE
    assert "[DONE]" in text
    data_lines = [ln[len("data: ") :] for ln in text.splitlines() if ln.startswith("data: ")]
    payloads = [json.loads(d) for d in data_lines if d.strip() and d.strip() != "[DONE]"]
    assert any("usage" in p and p["usage"] for p in payloads)


# ---------------------------------------------------------------------------
# AV5 — real embeddings: OpenAI list shape + EXACT summed usage.
# ---------------------------------------------------------------------------


async def test_AV5_real_embeddings_exact_tokens(stub_server: dict[str, Any]) -> None:
    # v25 task-3: AzureEmbeddingsProvider drops config= ctor arg; credentials via contextvar.
    # RIGHT-REASON RED: existing ctor still requires config= → TypeError until BUILD.
    provider = AzureEmbeddingsProvider(  # type: ignore[call-arg]
        token_provider_cache=None,
    )
    tok = set_provider_credential(_az_cred(stub_server["base_url"]))
    try:
        status, body = await provider.post_json(
            "/embeddings", {"model": "az-embed", "input": ["a", "bb"]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200, body
    assert body["object"] == "list"
    assert [d["index"] for d in body["data"]] == [0, 1]
    assert all(isinstance(d["embedding"], list) and d["embedding"] for d in body["data"])
    expected = len("a") + len("bb")  # 1 + 2 = 3 — exact, summed across the inputs
    assert body["usage"]["total_tokens"] == expected
    assert body["usage"]["prompt_tokens"] == expected


# ---------------------------------------------------------------------------
# AV6 — retry composes: 503 on attempt 1 → 200 on attempt 2; stub saw >= 2 (re-authed).
# ---------------------------------------------------------------------------


async def test_AV6_retry_to_success_composes(stub_server: dict[str, Any]) -> None:
    upstream = _chat_upstream(stub_server["base_url"], max_retries=2)
    tok = set_provider_credential(_az_cred(stub_server["base_url"]))
    try:
        status, body = await upstream.complete(
            {"model": "az-retry", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200, body  # transparently served after the retry
    async with httpx.AsyncClient(timeout=10) as client:
        counters = (await client.get(f"{stub_server['base_url']}/__counters")).json()
    assert counters.get("deploy-retry-once", 0) >= 2


# ---------------------------------------------------------------------------
# AV7 — content-filter triggers error-aware fallback to the healthy candidate.
# ---------------------------------------------------------------------------


async def test_AV7_content_filter_fallback_composes(stub_server: dict[str, Any]) -> None:
    upstream = _chat_upstream(stub_server["base_url"])
    router = FallbackModelRouter(
        upstream=upstream,
        model_groups={"az-group": ["az-cf", "az-ok"]},
        fallback_on_error=True,
    )
    tok = set_provider_credential(_az_cred(stub_server["base_url"]))
    try:
        status, body, served = await router.complete(
            {"model": "az-group", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200, body  # fell over from the content_filter 400 to the healthy deployment
    assert served == "az-ok"


# ---------------------------------------------------------------------------
# AV8 — security floor: the stub binds 127.0.0.1 (never 0.0.0.0).
# ---------------------------------------------------------------------------


def test_AV8_localhost_binding(stub_server: dict[str, Any]) -> None:
    assert stub_server["host"] == "127.0.0.1"
    assert stub.STUB_HOST == "127.0.0.1"


# ---------------------------------------------------------------------------
# AV9 — zero-regression floor: live script + overlay exist + idempotent.
# ---------------------------------------------------------------------------


def test_AV9_live_artifacts_exist_and_idempotent() -> None:
    live = _REPO_ROOT / "scripts" / "live_v21_verify.py"
    overlay = _REPO_ROOT / "infra" / "docker-compose.e2e.v21.yml"
    assert live.exists(), "operator live double-pass script must exist"
    assert overlay.exists(), "v21 e2e overlay must exist"

    live_src = live.read_text()
    assert "run_id" in live_src  # fresh identity per pass
    assert "__reset" in live_src  # reset between passes (double-pass idempotency)
    assert "127.0.0.1" in live_src  # asserts the stub binding

    overlay_src = overlay.read_text()
    assert "GATEWAY_AZURE_ENDPOINT" in overlay_src
    assert "host.docker.internal:9921" in overlay_src
