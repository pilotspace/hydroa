#!/usr/bin/env python3
"""v25 BYOK live double-pass — per-tenant provider credentials, end-to-end through Envoy.

Proves the v25 Bring-Your-Own-Key loop against THREE independent auth-verifying stubs
(bearer :9928 · bedrock :9927 · azure :9921), all on 127.0.0.1, end-to-end through the
real Envoy TLS/HTTP edge:

  B1 openrouter  the tenant PUTs its OpenRouter key → a chat call authenticates with THAT
                 per-tenant key (stub ACCEPTS the Bearer) → 200 chat.completion.
  B2 openai      same, OpenAI Bearer.
  B3 anthropic   same, Anthropic x-api-key.
  B4 google      same, Gemini x-goog-api-key.
  B5 bedrock     the tenant PUTs its AWS keys → a chat call is SigV4-signed with THEM → 200.
  B6 azure       the tenant PUTs its Azure api-key + endpoint → a chat call carries THAT
                 api-key to THAT deployment URL → 200.
  B7 fail-closed a SECOND tenant that configured NO key calls a model → 402
                 ERR_PROVIDER_KEY_MISSING (the platform holds no fallback key — BYOK).

The v25 difference vs v20/v21: credentials are NO LONGER injected via env/compose
(`GATEWAY_*_API_KEY` Settings fields were removed in tasks 2-3). Each B-step first
`PUT /admin/provider-keys/{provider}` with the credential the stub expects, THEN calls —
so a 200 proves per-request resolution from the tenant's Fernet-encrypted store reached
the adapter and was put on the wire. The stubs re-implement each provider's auth from spec
(they do NOT import gateway code), so every ACCEPT is a genuine independent cross-check; a
wrong/missing credential would 401 (proven in the no-docker earned-green suite, BV1).

Operator-run; NO production source change. Starts the 3 stubs in daemon threads, seeds the
catalog, restarts the gateway, then runs B1-B7.

run_id = int(time.time()) — changes every run; all tenant identities fresh per pass.

Double-pass close rule: run this script twice in sequence; both must exit 0. Each pass
resets every stub's counters first (idempotency) and uses a fresh run_id.

Usage (bring up the stack first):
    GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY must match the overlay (a Fernet key).

    docker compose \\
        -f infra/docker-compose.e2e.yml \\
        -f infra/docker-compose.e2e.v25.yml \\
        up --build -d --wait

    python3 scripts/live_v25_verify.py   # pass 1
    python3 scripts/live_v25_verify.py   # pass 2 (double-pass close rule)

    docker compose -f infra/docker-compose.e2e.yml -f infra/docker-compose.e2e.v25.yml down -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# HTTP edge (port 8080) to avoid TLS cert issues; Envoy still enforces JWT auth.
BASE = os.environ.get("SMOKE_BASE", "http://localhost:8080")

# Stub bind addresses (loopback ONLY — asserted at startup).
STUB_HOST = "127.0.0.1"
BEARER_PORT = 9928  # openrouter + openai + anthropic + google (one stub, path-routed)
BEDROCK_PORT = 9927
AZURE_PORT = 9921

# Azure endpoint as the GATEWAY container reaches the host azure stub.
AZURE_ENDPOINT = f"http://host.docker.internal:{AZURE_PORT}"
AZURE_API_VERSION = "2024-10-21"
AZURE_DEPLOYMENT = "deploy-chat"  # a plain 200 deployment in the v21 azure stub

# Client-surface model ids → provider (the catalog `provider` column drives dispatch).
MODELS: list[tuple[str, str, str, str]] = [
    # (model_id, display_name, modality, provider)
    ("or-chat", "v25 byok openrouter chat", "chat", "openrouter"),
    ("oai-chat", "v25 byok openai chat", "chat", "openai"),
    ("ant-chat", "v25 byok anthropic chat", "chat", "anthropic"),
    ("goo-chat", "v25 byok google chat", "chat", "google"),
    ("anthropic.claude-3-haiku-20240307-v1:0", "v25 byok bedrock chat", "chat", "bedrock"),
    ("az-chat", "v25 byok azure chat", "chat", "azure"),
]

EDGE_PACE_S = 0.1
EDGE_SETTLE_S = 2.0

PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"

# Per-run ID — embeds in every tenant email/key name (fresh identities per pass).
run_id = int(time.time())

RESULTS: list[tuple[str, bool, str]] = []

# ---------------------------------------------------------------------------
# Stub imports
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import v20_bedrock_stub  # noqa: E402 — after sys.path setup
import v21_azure_stub  # noqa: E402
import v25_bearer_stub  # noqa: E402

# Per-provider credentials the stubs expect (FAKE public test values — NOT real secrets).
PUT_BODIES: dict[str, dict[str, Any]] = {
    "openrouter": {"secret": v25_bearer_stub.EXPECTED_OPENROUTER},
    "openai": {"secret": v25_bearer_stub.EXPECTED_OPENAI},
    "anthropic": {"secret": v25_bearer_stub.EXPECTED_ANTHROPIC},
    "google": {"secret": v25_bearer_stub.EXPECTED_GOOGLE},
    "bedrock": {
        "access_key_id": v20_bedrock_stub.FAKE_ACCESS_KEY_ID,
        "secret_access_key": v20_bedrock_stub.FAKE_SECRET_ACCESS_KEY,
        "region": "us-east-1",
    },
    "azure": {
        "mode": "api_key",
        "endpoint": AZURE_ENDPOINT,
        "api_version": AZURE_API_VERSION,
        "deployment_map": {"az-chat": AZURE_DEPLOYMENT},
        "api_key": v21_azure_stub.EXPECTED_API_KEY,
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def record(criterion: str, ok: bool, note: str) -> None:
    RESULTS.append((criterion, ok, note))
    print(f"{'PASS' if ok else 'FAIL'}  {criterion}: {note}", flush=True)


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", "gateway", "-d", "gateway_e2e", "-tA", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _http(method: str, url: str, data: bytes | None = None, headers: dict[str, str] | None = None,
          timeout: float = 60.0) -> tuple[int, dict[str, Any], dict[str, str]]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 — edge/loopback under test
        raw = resp.read()
        body = json.loads(raw) if raw else {}
        return resp.status, body, dict(resp.getheaders())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except Exception:
            body = {}
        return exc.code, body, dict(exc.headers)
    except Exception as exc:
        return 0, {"error": str(exc)}, {}


def _post_json(path: str, payload: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any], dict[str, str]]:
    hdrs = {"Content-Type": "application/json", **headers}
    return _http("POST", f"{BASE}{path}", json.dumps(payload).encode(), hdrs)


def _put_json(path: str, payload: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any], dict[str, str]]:
    hdrs = {"Content-Type": "application/json", **headers}
    return _http("PUT", f"{BASE}{path}", json.dumps(payload).encode(), hdrs)


def _wait_stub_healthy(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s, _, _ = _http("GET", f"http://{STUB_HOST}:{port}/__health", timeout=3)
        if s == 200:
            print(f"  [stub] healthy on {STUB_HOST}:{port}")
            return
        time.sleep(0.5)
    raise RuntimeError(f"stub on :{port} did not become healthy within {timeout}s")


def _reset_stub(port: int) -> None:
    """POST /__reset to clear stub counters — double-pass idempotency."""
    s, _, _ = _http("POST", f"http://{STUB_HOST}:{port}/__reset", b"{}",
                    {"Content-Type": "application/json"}, timeout=5)
    print(f"  [stub :{port}] reset counters (status={s})")


def _bearer_counters() -> dict[str, int]:
    _, body, _ = _http("GET", f"http://{STUB_HOST}:{BEARER_PORT}/__counters", timeout=5)
    return {k: int(v) for k, v in body.items()} if isinstance(body, dict) else {}


def _seed_models() -> None:
    """Seed one model per provider into catalog + pricing_snapshots. Idempotent (ON CONFLICT)."""
    model_rows = ", ".join(
        f"('{mid}', '{name}', 200000, true, '{modality}', '{provider}', now(), now())"
        for mid, name, modality, provider in MODELS
    )
    psql(
        "INSERT INTO models (id, name, context_length, active, modality, provider, created_at, updated_at) "
        "VALUES " + model_rows
        + " ON CONFLICT (id) DO UPDATE SET active = true, modality = EXCLUDED.modality, provider = EXCLUDED.provider;"
    )
    snap_rows = ", ".join(
        f"(gen_random_uuid(), '{mid}', 0.000003, 0.000015, now(), 'per_token', NULL)"
        for mid, _, _, _ in MODELS
    )
    psql(
        "INSERT INTO pricing_snapshots (id, model_id, prompt_usd_per_token, completion_usd_per_token, "
        "captured_at, pricing_unit, unit_usd_per_unit) VALUES " + snap_rows + ";"
    )
    print(f"  [seed] {len(MODELS)} models + pricing seeded (one per provider, active=true)")


def _restart_gateway_and_wait(compose_files: list[str], timeout: float = 90.0) -> None:
    cmd = ["docker", "compose"]
    for f in compose_files:
        cmd += ["-f", f]
    cmd += ["restart", "gateway"]
    subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
    print("  [gateway] restart triggered; polling health ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["docker", "exec", GW_CONTAINER, "python", "-c",
                 "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                print("  [gateway] healthy after restart")
                return
        except Exception:
            pass
        time.sleep(2)
    print("  WARN: gateway health poll timed out; continuing")


def _signup_login_key(label: str) -> dict[str, str]:
    """Create a fresh tenant + owner JWT + API key. Returns {owner_jwt, api_key, email}."""
    email = f"v25-{label}-{run_id}@live.io"
    password = "v25-verify-pass"  # noqa: S105 — test harness only
    s, b, _ = _post_json("/admin/auth/signup",
                         {"tenant_name": f"V25-{label}-{run_id}", "email": email, "password": password}, {})
    assert s == 201, f"signup failed: {s} {b}"
    s, b, _ = _post_json("/admin/auth/login", {"email": email, "password": password}, {})
    assert s == 200, f"login failed: {s} {b}"
    owner_jwt = b["access_token"]
    owner_hdrs = {"Authorization": f"Bearer {owner_jwt}"}
    s, b, _ = _post_json("/admin/keys", {"name": f"v25-key-{label}-{run_id}"}, owner_hdrs)
    assert s == 201, f"key creation failed: {s} {b}"
    return {"owner_jwt": owner_jwt, "api_key": b["key"], "email": email}


def _chat(model: str, api_key: str) -> tuple[int, dict[str, Any], dict[str, str]]:
    time.sleep(EDGE_PACE_S)
    return _post_json(
        "/v1/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": f"hello-{run_id}"}]},
        {"Authorization": f"Bearer {api_key}"},
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    compose_files = ["infra/docker-compose.e2e.yml", "infra/docker-compose.e2e.v25.yml"]

    print(f"v25 BYOK live verify  run_id={run_id}  BASE={BASE}")
    print("=" * 64)

    # ── Start the 3 stubs (loopback only) ──────────────────────────────────
    bearer_srv = v25_bearer_stub.make_stub_server(BEARER_PORT)
    bedrock_srv = v20_bedrock_stub.make_stub_server(BEDROCK_PORT)
    azure_srv = v21_azure_stub.make_stub_server(AZURE_PORT)
    for name, srv in (("bearer", bearer_srv), ("bedrock", bedrock_srv), ("azure", azure_srv)):
        addr = srv.server_address[0]
        if addr != "127.0.0.1":
            print(f"HARD-STOP: {name} stub bound to {addr!r} (expected 127.0.0.1)", file=sys.stderr)
            sys.exit(1)
        print(f"  [security] {name} stub asserted loopback {addr} OK")
    v25_bearer_stub.start_stub_in_thread(bearer_srv)
    v20_bedrock_stub.start_stub_in_thread(bedrock_srv)
    v21_azure_stub.start_stub_in_thread(azure_srv)

    for port in (BEARER_PORT, BEDROCK_PORT, AZURE_PORT):
        _wait_stub_healthy(port)
        _reset_stub(port)  # double-pass idempotency: each pass starts from 0

    print("\n── Seeding catalog ──")
    _seed_models()

    print("\n── Restarting gateway ──")
    _restart_gateway_and_wait(compose_files)
    time.sleep(EDGE_SETTLE_S)

    # ── Tenant A: configures every provider key, then calls each ───────────
    print("\n── Tenant A (BYOK configured) ──")
    a = _signup_login_key("a")
    owner_hdrs = {"Authorization": f"Bearer {a['owner_jwt']}"}
    print(f"  tenant={a['email']}")

    for _mid, _name, _modality, provider in MODELS:
        s, b, _ = _put_json(f"/admin/provider-keys/{provider}", PUT_BODIES[provider], owner_hdrs)
        ok = s == 200
        record(f"PUT /admin/provider-keys/{provider}", ok, f"status={s}" + ("" if ok else f" {b}"))

    before = _bearer_counters()
    for mid, _name, _modality, provider in MODELS:
        s, b, _ = _chat(mid, a["api_key"])
        ok = s == 200 and isinstance(b, dict) and b.get("object") == "chat.completion"
        record(
            f"B:{provider} chat with per-tenant key → 200 chat.completion",
            ok,
            f"status={s} usage={b.get('usage') if isinstance(b, dict) else None}",
        )

    # Cross-check: the bearer stub independently ACCEPTED the 4 resolved bearer secrets.
    after = _bearer_counters()
    for provider in ("openrouter", "openai", "anthropic", "google"):
        delta = after.get(provider, 0) - before.get(provider, 0)
        record(f"bearer-stub ACCEPTED resolved {provider} key", delta >= 1, f"accepts={delta}")

    # ── Tenant B: configures NOTHING → fail-closed ─────────────────────────
    print("\n── Tenant B (no keys → fail-closed) ──")
    b_tenant = _signup_login_key("b")
    s, body, _ = _chat("or-chat", b_tenant["api_key"])
    code = body.get("code") or body.get("type") if isinstance(body, dict) else None
    fail_closed = s == 402 and (code == "ERR_PROVIDER_KEY_MISSING" or "PROVIDER_KEY_MISSING" in json.dumps(body))
    record("B7 no-key tenant → 402 ERR_PROVIDER_KEY_MISSING (fail-closed)", fail_closed, f"status={s} body={body}")

    # ── Summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for crit, ok, _note in RESULTS:
        print(f"  {'✓' if ok else '✗'} {crit}")
    print(f"\nv25 BYOK live verify: {passed}/{total} checks passed  (run_id={run_id})")
    if passed != total:
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
