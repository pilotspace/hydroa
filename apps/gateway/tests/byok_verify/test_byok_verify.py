"""Red suite for byok-live-verify (v25 task 6) — frozen contract.

EARNED-GREEN, no-docker integration verification that the v25 BYOK loop is WIRE-CORRECT:
every one of the 6 providers authenticates with the CALLING tenant's credential resolved
from the request-scoped contextvar (the v25 BYOK seam), proven against INDEPENDENT oracle
stubs over real TCP (127.0.0.1) — and fails closed (`ProviderKeyMissing`) when the contextvar
is unset.

Three independent stubs (none import gateway code), loaded from the repo-root scripts/ via
importlib so the operator-runnable artifacts double as the test harness:
  - scripts/v25_bearer_stub.py  (NEW) — verifies Authorization: Bearer (openrouter/openai),
    x-api-key (anthropic), x-goog-api-key (google) against per-provider FAKE secrets.
  - scripts/v20_bedrock_stub.py (REUSE) — independent SigV4 verifier.
  - scripts/v21_azure_stub.py   (REUSE) — independent api-key / AAD-minted-token oracle.

RED until BUILD creates scripts/v25_bearer_stub.py (importlib load raises FileNotFoundError)
and the live script + overlay (BV10 existence/idempotency asserts).
"""

from __future__ import annotations

import importlib.util
import json
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.provider_credentials import (
    AzureCredential,
    BearerCredential,
    BedrockCredential,
    ProviderKeyMissing,
)
from gateway.proxy.infrastructure.anthropic_upstream import AnthropicCompletionUpstream
from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream
from gateway.proxy.infrastructure.bedrock_upstream import BedrockCompletionUpstream
from gateway.proxy.infrastructure.gemini_upstream import GeminiCompletionUpstream
from gateway.proxy.infrastructure.openai_provider import OpenAIDirectProvider
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Load the independent stubs from the repo root (pure stdlib, no gateway import).
# RED: spec_from_file_location's loader raises FileNotFoundError until BUILD writes
# scripts/v25_bearer_stub.py.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]  # byok_verify→tests→gateway→apps→<root>


def _load_stub(filename: str, mod_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(mod_name, _REPO_ROOT / "scripts" / filename)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # RED here for the bearer stub until BUILD creates it
    return mod


bearer_stub = _load_stub("v25_bearer_stub.py", "v25_bearer_stub")
bedrock_stub = _load_stub("v20_bedrock_stub.py", "v20_bedrock_stub")
azure_stub = _load_stub("v21_azure_stub.py", "v21_azure_stub")

# Fake public test secrets — MUST equal the bearer stub's EXPECTED_* constants.
_OR = bearer_stub.EXPECTED_OPENROUTER
_OAI = bearer_stub.EXPECTED_OPENAI
_ANT = bearer_stub.EXPECTED_ANTHROPIC
_GOO = bearer_stub.EXPECTED_GOOGLE

_AZ_API_VERSION = "2024-10-21"
_AZ_DEPLOY_MAP = {"az-chat": "deploy-chat"}


# ---------------------------------------------------------------------------
# Stub server fixtures — session-scoped on ephemeral 127.0.0.1 ports; counters
# reset before each test for double-pass-style idempotency.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def bearer_server() -> Any:
    server: HTTPServer = bearer_stub.make_stub_server(port=0)
    thread = bearer_stub.start_stub_in_thread(server)
    host, port = server.server_address[0], server.server_address[1]
    try:
        yield {"server": server, "base_url": f"http://{host}:{port}", "host": host}
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def bedrock_server() -> Any:
    server: HTTPServer = bedrock_stub.make_stub_server(port=0)
    thread = bedrock_stub.start_stub_in_thread(server)
    host, port = server.server_address[0], server.server_address[1]
    try:
        yield {"server": server, "base_url": f"http://{host}:{port}", "host": host}
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def azure_server() -> Any:
    server: HTTPServer = azure_stub.make_stub_server(port=0)
    thread = azure_stub.start_stub_in_thread(server)
    host, port = server.server_address[0], server.server_address[1]
    try:
        yield {"server": server, "base_url": f"http://{host}:{port}", "host": host}
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _reset_counters(bearer_server: dict[str, Any], bedrock_server: dict[str, Any], azure_server: dict[str, Any]) -> None:
    for s in (bearer_server, bedrock_server, azure_server):
        try:
            httpx.post(f"{s['base_url']}/__reset", timeout=5)
        except Exception:  # noqa: BLE001 — best-effort reset; a stub without /__reset is fine
            pass


def _bearer_counters(base_url: str) -> dict[str, int]:
    return httpx.get(f"{base_url}/__counters", timeout=5).json()


# ---------------------------------------------------------------------------
# BV1 — the independent bearer oracle rejects wrong / missing credentials.
# ---------------------------------------------------------------------------


async def test_BV1_bearer_oracle_rejects_wrong_and_missing(bearer_server: dict[str, Any]) -> None:
    base = bearer_server["base_url"]
    body = json.dumps({"model": "m", "messages": []}).encode()
    surfaces = [
        ("/api/v1/chat/completions", "authorization", "Bearer WRONG"),  # openrouter
        ("/v1/chat/completions", "authorization", "Bearer WRONG"),  # openai
        ("/v1/messages", "x-api-key", "WRONG"),  # anthropic
        ("/v1beta/models/m:generateContent", "x-goog-api-key", "WRONG"),  # gemini
    ]
    async with httpx.AsyncClient(timeout=10) as client:
        for path, header, wrong in surfaces:
            r_wrong = await client.post(
                f"{base}{path}", content=body, headers={header: wrong, "content-type": "application/json"}
            )
            assert r_wrong.status_code == 401, f"{path} wrong-cred expected 401, got {r_wrong.status_code}"
            r_missing = await client.post(
                f"{base}{path}", content=body, headers={"content-type": "application/json"}
            )
            assert r_missing.status_code == 401, f"{path} no-cred expected 401, got {r_missing.status_code}"


# ---------------------------------------------------------------------------
# BV2-BV5 — each Bearer provider authenticates with the contextvar credential.
# ---------------------------------------------------------------------------


async def test_BV2_openrouter_contextvar_auth_accepted(bearer_server: dict[str, Any]) -> None:
    base = bearer_server["base_url"]
    upstream = OpenRouterCompletionUpstream(base_url=f"{base}/api/v1")
    tok = set_provider_credential(BearerCredential(secret=_OR))
    try:
        status, body = await upstream.complete(
            {"model": "or-chat", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200, body
    assert body["object"] == "chat.completion"
    assert _bearer_counters(base).get("openrouter", 0) >= 1


async def test_BV3_openai_contextvar_auth_accepted(bearer_server: dict[str, Any]) -> None:
    base = bearer_server["base_url"]
    provider = OpenAIDirectProvider(base_url=f"{base}/v1")
    tok = set_provider_credential(BearerCredential(secret=_OAI))
    try:
        status, body = await provider.post_json(
            "/chat/completions", {"model": "oai-chat", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200, body
    assert body["object"] == "chat.completion"
    assert _bearer_counters(base).get("openai", 0) >= 1


async def test_BV4_anthropic_contextvar_auth_accepted(bearer_server: dict[str, Any]) -> None:
    base = bearer_server["base_url"]
    upstream = AnthropicCompletionUpstream(base_url=f"{base}/v1")
    tok = set_provider_credential(BearerCredential(secret=_ANT))
    try:
        status, body = await upstream.complete(
            {"model": "ant-chat", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200, body  # x-api-key matched (the stub 401s otherwise)
    assert "choices" in body  # translated to OpenAI chat.completion
    assert _bearer_counters(base).get("anthropic", 0) >= 1


async def test_BV5_gemini_contextvar_auth_accepted(bearer_server: dict[str, Any]) -> None:
    base = bearer_server["base_url"]
    upstream = GeminiCompletionUpstream(base_url=f"{base}/v1beta")
    tok = set_provider_credential(BearerCredential(secret=_GOO))
    try:
        status, body = await upstream.complete(
            {"model": "goo-chat", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200, body  # x-goog-api-key matched
    assert "choices" in body
    assert _bearer_counters(base).get("google", 0) >= 1


# ---------------------------------------------------------------------------
# BV6 — Bedrock authenticates with SigV4 derived from the contextvar credential.
# ---------------------------------------------------------------------------


async def test_BV6_bedrock_contextvar_sigv4_accepted(bedrock_server: dict[str, Any]) -> None:
    upstream = BedrockCompletionUpstream(endpoint_url=bedrock_server["base_url"])
    cred = BedrockCredential(
        access_key_id=bedrock_stub.FAKE_ACCESS_KEY_ID,
        secret_access_key=bedrock_stub.FAKE_SECRET_ACCESS_KEY,
        region="us-east-1",
    )
    tok = set_provider_credential(cred)
    try:
        status, body = await upstream.complete(
            {"model": "anthropic.claude-3-haiku-20240307-v1:0", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    # 200 (not 403) proves the v20 stub's INDEPENDENT SigV4 verify passed against the
    # contextvar credential — the request was signed with the tenant's resolved AWS creds.
    assert status == 200, body


# ---------------------------------------------------------------------------
# BV7 — Azure authenticates with api-key (and AAD) from the contextvar credential.
# ---------------------------------------------------------------------------


async def test_BV7_azure_contextvar_apikey_and_aad_accepted(azure_server: dict[str, Any]) -> None:
    base = azure_server["base_url"]
    upstream = AzureCompletionUpstream(token_provider_cache=None)

    # api_key mode — the v21 stub's api-key oracle must ACCEPT.
    api_cred = AzureCredential(
        mode="api_key",
        endpoint=base,
        api_version=_AZ_API_VERSION,
        deployment_map=dict(_AZ_DEPLOY_MAP),
        api_key=azure_stub.EXPECTED_API_KEY,
    )
    tok = set_provider_credential(api_cred)
    try:
        status, body = await upstream.complete(
            {"model": "az-chat", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200, body
    assert body["object"] == "chat.completion"

    # aad mode — the stub MINTS the token at its own endpoint (authority=stub) and accepts
    # Bearer == that minted token: proves the real AAD token path end-to-end from the contextvar.
    aad_cred = AzureCredential(
        mode="aad",
        endpoint=base,
        api_version=_AZ_API_VERSION,
        deployment_map=dict(_AZ_DEPLOY_MAP),
        tenant_id="tenant-1",
        client_id="c",
        client_secret=azure_stub.EXPECTED_CLIENT_SECRET,
        authority=base,
    )
    tok = set_provider_credential(aad_cred)
    try:
        status2, body2 = await upstream.complete(
            {"model": "az-chat", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status2 == 200, body2


# ---------------------------------------------------------------------------
# BV8 — fail-closed: with the contextvar unset, every adapter raises ProviderKeyMissing
# and makes NO upstream request.
# ---------------------------------------------------------------------------


async def test_BV8_fail_closed_contextvar_unset(
    bearer_server: dict[str, Any], bedrock_server: dict[str, Any], azure_server: dict[str, Any]
) -> None:
    base = bearer_server["base_url"]
    payload = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}

    cases = [
        OpenRouterCompletionUpstream(base_url=f"{base}/api/v1").complete(payload),
        OpenAIDirectProvider(base_url=f"{base}/v1").post_json("/chat/completions", payload),
        AnthropicCompletionUpstream(base_url=f"{base}/v1").complete(payload),
        GeminiCompletionUpstream(base_url=f"{base}/v1beta").complete(payload),
        BedrockCompletionUpstream(endpoint_url=bedrock_server["base_url"]).complete(
            {"model": "anthropic.claude-3-haiku-20240307-v1:0", "messages": payload["messages"]}
        ),
        AzureCompletionUpstream(token_provider_cache=None).complete(
            {"model": "az-chat", "messages": payload["messages"]}
        ),
    ]
    for coro in cases:
        with pytest.raises(ProviderKeyMissing) as exc_info:
            await coro
        assert exc_info.value.code == "ERR_PROVIDER_KEY_MISSING"


# ---------------------------------------------------------------------------
# BV9 — security floor: the bearer stub binds 127.0.0.1 (never 0.0.0.0).
# ---------------------------------------------------------------------------


def test_BV9_localhost_binding(bearer_server: dict[str, Any]) -> None:
    assert bearer_server["host"] == "127.0.0.1"
    assert bearer_stub.STUB_HOST == "127.0.0.1"


# ---------------------------------------------------------------------------
# BV10 — zero-regression floor: the live script + overlay exist + are idempotent.
# ---------------------------------------------------------------------------


def test_BV10_live_artifacts_exist_and_idempotent() -> None:
    live = _REPO_ROOT / "scripts" / "live_v25_verify.py"
    overlay = _REPO_ROOT / "infra" / "docker-compose.e2e.v25.yml"
    assert live.exists(), "operator live double-pass script must exist"
    assert overlay.exists(), "v25 e2e overlay must exist"

    live_src = live.read_text()
    assert "run_id" in live_src  # fresh identity per pass
    assert "__reset" in live_src  # reset between passes (double-pass idempotency)
    assert "127.0.0.1" in live_src  # asserts the stub binding
    assert "/admin/provider-keys" in live_src  # BYOK write step (set the tenant key)

    overlay_src = overlay.read_text()
    assert "GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY" in overlay_src  # store needs the Fernet key
    assert "host.docker.internal" in overlay_src  # providers redirected to the host stubs
    # BYOK = NO env provider keys (the platform holds none).
    assert "_API_KEY:" not in overlay_src, "overlay must not set any GATEWAY_*_API_KEY (BYOK)"
