"""Red suite for vertex-verify (vertex-adapter TASK.md §4) — frozen contract.

EARNED-GREEN, no-docker integration verification of the vertex-adapter surface (JWT-bearer
token mint + generateContent + streamGenerateContent) against an INDEPENDENT RS256 JWT
verifier over real TCP (127.0.0.1). The stub reimplements RSA PKCS#1v1.5/SHA-256 signature
verification from scratch (it does NOT import pyjwt/cryptography), so accepting the real
adapter's PyJWT-signed assertion is a genuine cross-check — the same independent-oracle
philosophy as bedrock_verify's SigV4 stub / azure_verify's stub.

The stub lives at the repo-root ``scripts/vertex_stub.py`` (pure stdlib, self-contained)
and is loaded here via importlib so the single operator-runnable artifact is also the test
harness (mirrors tests/bedrock_verify/test_bedrock_verify.py exactly).

RED until BUILD creates ``scripts/vertex_stub.py`` (importlib load raises FileNotFoundError).
"""

from __future__ import annotations

import importlib.util
import json
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import PublicFormat

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.provider_credentials import GoogleServiceAccountCredential
from gateway.proxy.infrastructure.vertex_ad import VertexServiceAccountConfig, VertexTokenProvider
from gateway.proxy.infrastructure.vertex_upstream import VertexCompletionUpstream

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Load the independent stub from the repo root (pure stdlib, no gateway import).
# RED: spec_from_file_location's loader raises FileNotFoundError until BUILD writes
# scripts/vertex_stub.py.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]  # vertex_verify→tests→gateway→apps→<root>
_STUB_PATH = _REPO_ROOT / "scripts" / "vertex_stub.py"


def _load_stub() -> Any:
    spec = importlib.util.spec_from_file_location("vertex_stub", _STUB_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # RED here: FileNotFoundError until the stub exists
    return mod


stub = _load_stub()

# ---------------------------------------------------------------------------
# A REAL RSA keypair, generated fresh per test session (never a real service-
# account key — a synthetic one, exactly the shape a real GCP SA key would be).
# ---------------------------------------------------------------------------

_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_KEY_PEM = _RSA_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
_PUBLIC_KEY_PEM = (
    _RSA_KEY.public_key()
    .public_bytes(encoding=serialization.Encoding.PEM, format=PublicFormat.SubjectPublicKeyInfo)
    .decode()
)

_CLIENT_EMAIL = "verify-sa@verify-project.iam.gserviceaccount.com"
_PROJECT_ID = "verify-project"

_M_CHAT = "eu.gemini-2.5-flash"
_BARE_CHAT = "gemini-2.5-flash"
_M_STREAM = "ap.gemini-2.5-pro"
_M_RETRY = "eu.gemini-2.5-flash-retry"
_M_NOTFOUND = "eu.gemini-2.5-flash-notfound-region"


@pytest.fixture(scope="session")
def stub_server() -> Any:
    server: HTTPServer = stub.make_stub_server(port=0)  # 0 → OS-assigned ephemeral port
    thread = stub.start_stub_in_thread(server)
    host, port = server.server_address[0], server.server_address[1]
    base_url = f"http://{host}:{port}"
    stub.configure(
        public_key_pem=_PUBLIC_KEY_PEM,
        client_email=_CLIENT_EMAIL,
        project_id=_PROJECT_ID,
        expected_aud=f"{base_url}/token",
    )
    try:
        yield {"server": server, "base_url": base_url, "host": host, "port": port}
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _reset_stub(stub_server: dict[str, Any]) -> None:
    httpx.post(f"{stub_server['base_url']}/__reset", timeout=5)


def _cred() -> GoogleServiceAccountCredential:
    return GoogleServiceAccountCredential(
        project_id=_PROJECT_ID,
        client_email=_CLIENT_EMAIL,
        private_key=_PRIVATE_KEY_PEM,
        private_key_id="verify-key-1",
    )


def _token_provider(base_url: str) -> VertexTokenProvider:
    """A REAL VertexTokenProvider pointed at the local stub's /token endpoint (config
    override — mirrors AzureADConfig.authority's own local-stub test override)."""
    config = VertexServiceAccountConfig(
        project_id=_PROJECT_ID,
        client_email=_CLIENT_EMAIL,
        private_key=_PRIVATE_KEY_PEM,
        private_key_id="verify-key-1",
        token_uri=f"{base_url}/token",
    )
    return VertexTokenProvider(config=config)


class _FixedCache:
    """Stub token-provider cache that returns a fixed provider (bypasses the real
    OAuth2/oauth2.googleapis.com host — points at the local stub instead)."""

    def __init__(self, provider: VertexTokenProvider) -> None:
        self._provider = provider

    def get_or_create(self, config: object, tenant_id: object | None = None) -> VertexTokenProvider:
        return self._provider


def _upstream(stub_server: dict[str, Any], *, max_retries: int = 0) -> VertexCompletionUpstream:
    upstream = VertexCompletionUpstream(
        token_provider_cache=_FixedCache(_token_provider(stub_server["base_url"])),
        max_retries=max_retries,
        backoff_base=0.0,
    )
    return upstream


def _patch_url(upstream: VertexCompletionUpstream, base_url: str) -> None:
    """Monkeypatch the module-level URL builder used by this adapter instance so the
    generateContent/streamGenerateContent calls land on the LOCAL stub instead of the
    real *-aiplatform.googleapis.com host — mirrors bedrock's own endpoint_url= override
    pattern (Vertex's own URL is built from a fixed host template, not a ctor arg, so we
    monkeypatch the module function for the duration of this suite instead)."""
    import gateway.proxy.infrastructure.vertex_upstream as vertex_upstream_mod

    def _local_build_url(location: str, project_id: str, bare_model: str, *, stream: bool) -> str:
        action = "streamGenerateContent" if stream else "generateContent"
        return f"{base_url}/v1/projects/{project_id}/locations/{location}/publishers/google/models/{bare_model}:{action}"

    vertex_upstream_mod._build_url = _local_build_url  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# VV1 — RS256 signature verification is byte-correct (independent oracle self-check).
# ---------------------------------------------------------------------------


async def test_VV1_stub_rsa_verifier_accepts_a_known_good_signature() -> None:
    """A hand-signed RS256 JWT (via the REAL pyjwt) is accepted by the stub's
    from-scratch verifier — proves the independent verifier is correct BEFORE it
    judges any gateway-produced signature."""
    import time

    import jwt

    now = int(time.time())
    token = jwt.encode(
        {"iss": "x", "sub": "x", "aud": "y", "iat": now, "exp": now + 60},
        _PRIVATE_KEY_PEM,
        algorithm="RS256",
    )
    n, e = stub.parse_rsa_public_key_pem(_PUBLIC_KEY_PEM)
    payload = stub.verify_rs256_jwt(token, n=n, e=e)
    assert payload is not None
    assert payload["iss"] == "x"


async def test_VV1b_stub_rsa_verifier_rejects_tampered_signature() -> None:
    import time

    import jwt

    now = int(time.time())
    token = jwt.encode(
        {"iss": "x", "sub": "x", "aud": "y", "iat": now, "exp": now + 60},
        _PRIVATE_KEY_PEM,
        algorithm="RS256",
    )
    header, payload_b64, sig_b64 = token.split(".")
    tampered = f"{header}.{payload_b64}.{sig_b64[:-4]}AAAA"
    n, e = stub.parse_rsa_public_key_pem(_PUBLIC_KEY_PEM)
    assert stub.verify_rs256_jwt(tampered, n=n, e=e) is None


# ---------------------------------------------------------------------------
# VV2 — real token mint accepted by the independent verifier.
# ---------------------------------------------------------------------------


async def test_VV2_real_token_mint_accepted(stub_server: dict[str, Any]) -> None:
    provider = _token_provider(stub_server["base_url"])
    try:
        token = await provider.get_token()
        assert token.startswith("stub-access-token-")
    finally:
        await provider.aclose()


async def test_VV3_tampered_assertion_rejected(stub_server: dict[str, Any]) -> None:
    """A hand-crafted invalid assertion is rejected by the stub with invalid_grant."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{stub_server['base_url']}/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": "not.a.validjwt",
            },
        )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


async def test_VV3b_missing_assertion_rejected(stub_server: dict[str, Any]) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{stub_server['base_url']}/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer"},
        )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# VV4 — real chat request accepted by the stub; OpenAI shape + billing.
# ---------------------------------------------------------------------------


async def test_VV4_real_chat_request_accepted_with_billing(stub_server: dict[str, Any]) -> None:
    _patch_url(_upstream(stub_server), stub_server["base_url"])
    upstream = _upstream(stub_server)
    tok = set_provider_credential(_cred())
    try:
        status, body = await upstream.complete(
            {"model": _M_CHAT, "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200, body
    assert body["object"] == "chat.completion"
    usage = body["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


# ---------------------------------------------------------------------------
# VV5 — real streaming request accepted; OpenAI SSE ends with usage + [DONE].
# ---------------------------------------------------------------------------


async def test_VV5_real_streaming_accepted_with_usage(stub_server: dict[str, Any]) -> None:
    _patch_url(_upstream(stub_server), stub_server["base_url"])
    upstream = _upstream(stub_server)
    tok = set_provider_credential(_cred())
    chunks: list[bytes] = []
    try:
        async for chunk in upstream.stream(
            {"model": _M_STREAM, "messages": [{"role": "user", "content": "hi"}], "stream": True}
        ):
            chunks.append(chunk)
    finally:
        reset_provider_credential(tok)
    text = b"".join(chunks).decode("utf-8")
    assert "chat.completion.chunk" in text
    assert "[DONE]" in text
    data_lines = [ln[len("data: ") :] for ln in text.splitlines() if ln.startswith("data: ")]
    payloads = [json.loads(d) for d in data_lines if d.strip() and d.strip() != "[DONE]"]
    assert any("usage" in p and p["usage"] for p in payloads)


# ---------------------------------------------------------------------------
# VV6 — retry composes: 503 on attempt 1 → 200 on attempt 2.
# ---------------------------------------------------------------------------


async def test_VV6_retry_to_success_composes(stub_server: dict[str, Any]) -> None:
    _patch_url(_upstream(stub_server), stub_server["base_url"])
    upstream = _upstream(stub_server, max_retries=2)
    tok = set_provider_credential(_cred())
    try:
        status, body = await upstream.complete(
            {"model": _M_RETRY, "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200, body
    async with httpx.AsyncClient(timeout=10) as client:
        counters = (await client.get(f"{stub_server['base_url']}/__counters")).json()
    bare_retry_id = "gemini-2.5-flash-retry"
    assert counters.get(bare_retry_id, 0) >= 2


# ---------------------------------------------------------------------------
# VV7 — regional-404 passthrough verbatim, no exception.
# ---------------------------------------------------------------------------


async def test_VV7_regional_404_passthrough(stub_server: dict[str, Any]) -> None:
    _patch_url(_upstream(stub_server), stub_server["base_url"])
    upstream = _upstream(stub_server)
    tok = set_provider_credential(_cred())
    try:
        status, body = await upstream.complete(
            {"model": _M_NOTFOUND, "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 404
    assert body["error"]["type"] == "not_found"


# ---------------------------------------------------------------------------
# VV8 — unauthorized (no Bearer / unknown token) rejected on generateContent.
# ---------------------------------------------------------------------------


async def test_VV8_generate_content_requires_a_stub_minted_token(
    stub_server: dict[str, Any],
) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{stub_server['base_url']}/v1/projects/{_PROJECT_ID}/locations/europe-west4"
            f"/publishers/google/models/{_BARE_CHAT}:generateContent",
            json={},
            headers={"Authorization": "Bearer not-a-real-token"},
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# VV9 — zero-regression floor: stub binds 127.0.0.1; live script exists.
# ---------------------------------------------------------------------------


async def test_VV9_localhost_binding_and_live_script_exists(stub_server: dict[str, Any]) -> None:
    assert stub_server["host"] == "127.0.0.1"  # NEVER 0.0.0.0
    assert stub.STUB_HOST == "127.0.0.1"

    live = _REPO_ROOT / "scripts" / "live_vertex_verify.py"
    assert live.exists(), "operator live-verify script must exist"
    live_src = live.read_text()
    assert "GATEWAY_VERTEX_LIVE_SA_JSON" in live_src
    # the real (non-stub) operator script must reference the real regional Vertex host
    assert "aiplatform.googleapis.com" in live_src
