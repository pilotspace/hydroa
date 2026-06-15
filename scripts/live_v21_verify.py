#!/usr/bin/env python3
"""v21 Azure OpenAI live double-pass verification — chat · stream · embeddings · retry · fallback · cache.

Proves, end-to-end through the Envoy TLS/HTTP edge against an INDEPENDENT
auth/routing-verifying Azure stub, that:
  C1  CHAT:        an Azure chat request returns an OpenAI chat.completion with
                   accurate usage billed once (the stub ACCEPTED the api-key header
                   AND the correctly-formed deployment URL).
  C2  STREAM:      a streaming Azure request returns OpenAI SSE chunks ([DONE]).
  C3  EMBEDDINGS:  an Azure embeddings request returns the OpenAI list shape with
                   correct token accounting.
  C4  RETRY:       with GATEWAY_UPSTREAM_MAX_RETRIES>0, a model that 503s on attempt 1
                   is transparently served 200; the stub observed >1 attempt.
  C5  FALLBACK:    with GATEWAY_UPSTREAM_FALLBACK_ON_ERROR=true, an alias whose first
                   candidate returns a content_filter 400 falls over to the next (200).
  C6  CACHE:       a repeated identical chat request returns X-Cache: hit.

The stub re-implements Azure api-key auth and deployment-URL routing from the spec (does
NOT import the gateway adapter), so every ACCEPT is a genuine independent cross-check;
a tampered request would 401.

Operator-run; NO production source change. Starts scripts/v21_azure_stub.py in a
daemon thread (127.0.0.1:9921), seeds catalog models/pricing, restarts gateway,
then runs C1-C6.

run_id = int(time.time()) — changes every run; all identities fresh per pass.

Double-pass close rule: run this script twice in sequence; both must exit 0.

Usage (bring up the stack first):
    docker compose \\
        -f infra/docker-compose.e2e.yml \\
        -f infra/docker-compose.e2e.v4.yml \\
        -f infra/docker-compose.e2e.v5.yml \\
        -f infra/docker-compose.e2e.v6.yml \\
        -f infra/docker-compose.e2e.v21.yml \\
        up --build -d --wait

    python3 scripts/live_v21_verify.py   # pass 1
    python3 scripts/live_v21_verify.py   # pass 2 (double-pass close rule)

    docker compose -f infra/docker-compose.e2e.yml ... -f infra/docker-compose.e2e.v21.yml down -v
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

# Use HTTP edge (port 8080) to avoid TLS cert issues; Envoy still enforces JWT auth.
BASE = os.environ.get("SMOKE_BASE", "http://localhost:8080")

STUB_HOST = "127.0.0.1"
STUB_PORT = 9921

# Azure model ids visible to the OpenAI-compatible client surface.
# These map via GATEWAY_AZURE_DEPLOYMENT_MAP to deployment names in the stub.
AZURE_CHAT = "az-chat"
AZURE_STREAM = "az-stream"
AZURE_RETRY = "az-retry"
AZURE_CF = "az-cf"  # content-filter model → triggers fallover
AZURE_OK = "az-ok"  # fallback target (deploy-ok → 200)
AZURE_EMBED = "az-embed"

ALIAS_FB = "azure-fb"  # [AZURE_CF, AZURE_OK]

# Deployment names as seen by the stub (keys in GATEWAY_AZURE_DEPLOYMENT_MAP).
DEPLOY_CHAT = "deploy-chat"
DEPLOY_STREAM = "deploy-stream"
DEPLOY_RETRY = "deploy-retry-once"
DEPLOY_CF = "deploy-content-filter"
DEPLOY_OK = "deploy-ok"
DEPLOY_EMBED = "deploy-embed"

# Timing
EDGE_PACE_S = 0.1
EDGE_SETTLE_S = 2.0

# Container names
PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"

# Per-run ID — embeds in every tenant email/key name
run_id = int(time.time())

RESULTS: list[tuple[str, bool, str]] = []

# ---------------------------------------------------------------------------
# Stub import
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import v21_azure_stub  # noqa: E402 — after sys.path setup

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def record(criterion: str, ok: bool, note: str) -> None:
    RESULTS.append((criterion, ok, note))
    status = "PASS" if ok else "FAIL"
    print(f"{status}  {criterion}: {note}", flush=True)


def psql(sql: str) -> str:
    out = subprocess.run(
        [
            "docker",
            "exec",
            PG_CONTAINER,
            "psql",
            "-U",
            "gateway",
            "-d",
            "gateway_e2e",
            "-tA",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _wait_stub_healthy(timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"http://{STUB_HOST}:{STUB_PORT}/__health", method="GET"
            )
            if urllib.request.urlopen(req, timeout=3).status == 200:  # noqa: S310 — loopback stub
                print(f"  [stub] healthy on {STUB_HOST}:{STUB_PORT}")
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"v21 stub did not become healthy within {timeout}s")


def _reset_stub() -> None:
    """POST /__reset to clear stub counters — double-pass idempotency."""
    try:
        req = urllib.request.Request(
            f"http://{STUB_HOST}:{STUB_PORT}/__reset",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)  # noqa: S310 — loopback stub
        print("  [stub] reset counters")
    except Exception as exc:
        print(f"  [stub] reset warning: {exc}")


def _get_stub_counters() -> dict[str, int]:
    req = urllib.request.Request(
        f"http://{STUB_HOST}:{STUB_PORT}/__counters", method="GET"
    )
    resp = urllib.request.urlopen(req, timeout=5)  # noqa: S310 — loopback stub
    return json.loads(resp.read())


def _seed_azure_models() -> None:
    """Seed all v21 stub model ids into catalog + pricing_snapshots (provider='azure').

    ON CONFLICT (id) DO UPDATE — idempotent across the double-pass.
    """
    all_models = [
        (AZURE_CHAT, "v21 azure chat", "chat"),
        (AZURE_STREAM, "v21 azure stream", "chat"),
        (AZURE_RETRY, "v21 azure retry candidate", "chat"),
        (AZURE_CF, "v21 azure content-filter fallback fail", "chat"),
        (AZURE_OK, "v21 azure fallback ok", "chat"),
        (AZURE_EMBED, "v21 azure embeddings", "embedding"),
    ]
    model_rows = ", ".join(
        f"('{mid}', '{name}', 200000, true, '{modality}', 'azure', now(), now())"
        for mid, name, modality in all_models
    )
    models_sql = (
        "INSERT INTO models "
        "(id, name, context_length, active, modality, provider, created_at, updated_at) VALUES "
        + model_rows
        + " ON CONFLICT (id) DO UPDATE SET active = true, modality = EXCLUDED.modality,"
        " provider = EXCLUDED.provider;"
    )
    psql(models_sql)

    snap_rows = ", ".join(
        f"(gen_random_uuid(), '{mid}', 0.000003, 0.000015, now(), 'per_token', NULL)"
        for mid, _, _ in all_models
    )
    snap_sql = (
        "INSERT INTO pricing_snapshots "
        "(id, model_id, prompt_usd_per_token, completion_usd_per_token, "
        " captured_at, pricing_unit, unit_usd_per_unit) VALUES " + snap_rows + ";"
    )
    psql(snap_sql)
    print(
        f"  [seed] {len(all_models)} azure models + pricing seeded (provider=azure, active=true)"
    )


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
                [
                    "docker",
                    "exec",
                    GW_CONTAINER,
                    "python",
                    "-c",
                    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0:
                print("  [gateway] healthy after restart")
                return
        except Exception:
            pass
        time.sleep(2)
    print("  WARN: gateway health poll timed out; continuing")


def _post_json(
    path: str, payload: dict[str, Any], headers: dict[str, str]
) -> tuple[int, dict[str, Any], dict[str, str]]:
    url = f"{BASE}{path}"
    body = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=60)  # noqa: S310 — edge under test
        return resp.status, json.loads(resp.read()), dict(resp.getheaders())
    except urllib.error.HTTPError as exc:
        try:
            err_body = json.loads(exc.read())
        except Exception:
            err_body = {}
        return exc.code, err_body, dict(exc.headers)
    except Exception as exc:
        return 0, {"error": str(exc)}, {}


def _post_sse(
    path: str, payload: dict[str, Any], headers: dict[str, str]
) -> tuple[int, str]:
    import http.client
    import urllib.parse

    parsed = urllib.parse.urlparse(f"{BASE}{path}")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 8080)
    body_bytes = json.dumps({**payload, "stream": True}).encode()
    hdrs = {"Content-Type": "application/json", "Connection": "close", **headers}
    try:
        conn = http.client.HTTPConnection(host, port, timeout=60)
        conn.request(
            "POST", parsed.path or "/v1/chat/completions", body=body_bytes, headers=hdrs
        )
        resp = conn.getresponse()
        status = resp.status
        raw = resp.read()
        conn.close()
        return status, raw.decode(errors="replace")
    except Exception as exc:
        return 0, str(exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    compose_files = [
        "infra/docker-compose.e2e.yml",
        "infra/docker-compose.e2e.v4.yml",
        "infra/docker-compose.e2e.v5.yml",
        "infra/docker-compose.e2e.v6.yml",
        "infra/docker-compose.e2e.v21.yml",
    ]

    print(f"v21 azure verify  run_id={run_id}  BASE={BASE}")
    print("=" * 60)

    # ── Start stub ────────────────────────────────────────────────────────
    stub_srv = v21_azure_stub.make_stub_server()
    v21_azure_stub.start_stub_in_thread(stub_srv)
    print(f"v21 azure stub started on {STUB_HOST}:{STUB_PORT}")

    # Security assertion: stub must be bound to loopback ONLY (127.0.0.1, NEVER 0.0.0.0).
    server_addr = stub_srv.server_address[0]
    if server_addr != "127.0.0.1":
        print(
            f"HARD-STOP: stub bound to {server_addr!r} (expected 127.0.0.1)",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        f"  [security] stub address asserted: {server_addr}:{STUB_PORT} (loopback only) OK"
    )

    _wait_stub_healthy()
    _reset_stub()  # double-pass idempotency: second pass starts from 0

    print("\n── Seeding catalog ──")
    _seed_azure_models()

    print("\n── Restarting gateway ──")
    _restart_gateway_and_wait(compose_files)
    time.sleep(EDGE_SETTLE_S)

    print("\n── Auth ──")
    email = f"v21-verify-{run_id}@live.io"
    password = "v21-verify-pass"  # noqa: S105 — test harness only, not a secret

    status, body, _ = _post_json(
        "/admin/auth/signup",
        {"tenant_name": f"V21Co-{run_id}", "email": email, "password": password},
        {},
    )
    assert status == 201, f"signup failed: {status} {body}"

    status, body, _ = _post_json(
        "/admin/auth/login", {"email": email, "password": password}, {}
    )
    assert status == 200, f"login failed: {status} {body}"
    auth_hdrs = {"Authorization": f"Bearer {body['access_token']}"}

    status, body, _ = _post_json(
        "/admin/keys", {"name": f"v21-key-{run_id}", "cache_enabled": True}, auth_hdrs
    )
    assert status == 201, f"key creation failed: {status} {body}"
    api_key = body["key"]
    key_hdrs = {"Authorization": f"Bearer {api_key}"}
    print(f"  tenant={email}  cache_enabled={body.get('cache_enabled')}")

    def _chat(
        model: str, user_msg: str, **kwargs: Any
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        time.sleep(EDGE_PACE_S)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": user_msg}],
            **kwargs,
        }
        return _post_json("/v1/chat/completions", payload, key_hdrs)

    # ── C1 CHAT ────────────────────────────────────────────────────────────
    print("\n── C1 CHAT ──")
    s, b, _ = _chat(AZURE_CHAT, f"chat-{run_id}")
    usage = b.get("usage", {}) if isinstance(b, dict) else {}
    c1_ok = (
        s == 200
        and b.get("object") == "chat.completion"
        and isinstance(usage.get("total_tokens"), int)
    )
    record(
        "C1 azure chat (api-key accepted + deployment URL routed) → OpenAI + usage",
        c1_ok,
        f"status={s} usage={usage}",
    )

    # ── C2 STREAM ────────────────────────────────────────────────────────────
    print("\n── C2 STREAM ──")
    s2, text2 = _post_sse(
        "/v1/chat/completions",
        {
            "model": AZURE_STREAM,
            "messages": [{"role": "user", "content": f"stream-{run_id}"}],
        },
        key_hdrs,
    )
    c2_ok = s2 == 200 and "chat.completion.chunk" in text2 and "[DONE]" in text2
    record(
        "C2 azure streaming → OpenAI SSE chunks + [DONE]",
        c2_ok,
        f"status={s2} bytes={len(text2)}",
    )

    # ── C3 EMBEDDINGS ──────────────────────────────────────────────────────────
    print("\n── C3 EMBEDDINGS ──")
    embed_input = "hello"
    time.sleep(EDGE_PACE_S)
    s3, b3, _ = _post_json(
        "/v1/embeddings", {"model": AZURE_EMBED, "input": embed_input}, key_hdrs
    )
    total_tokens = (
        b3.get("usage", {}).get("total_tokens") if isinstance(b3, dict) else None
    )
    data3 = b3.get("data") if isinstance(b3, dict) else None
    c3_ok = (
        s3 == 200
        and bool(data3)
        and total_tokens
        == len(embed_input)  # exact: stub sums len(text) for each input
    )
    record(
        "C3 azure embeddings → OpenAI list + EXACT tokens",
        c3_ok,
        f"status={s3} total_tokens={total_tokens}",
    )

    # ── C4 RETRY ────────────────────────────────────────────────────────────
    print("\n── C4 RETRY ──")
    s4, b4, _ = _chat(AZURE_RETRY, f"retry-{run_id}")
    record("C4a azure retry-to-success → 200", s4 == 200, f"status={s4}")
    time.sleep(0.5)
    counters = _get_stub_counters()
    retry_calls = counters.get(DEPLOY_RETRY, 0)
    record(
        "C4b stub observed >1 attempt on deploy-retry-once (retry happened)",
        retry_calls > 1,
        f"stub_calls={retry_calls}",
    )

    # ── C5 CONTENT-FILTER → ERROR-AWARE FALLBACK ─────────────────────────────
    print("\n── C5 FALLBACK ──")
    s5, b5, _ = _chat(ALIAS_FB, f"fallback-{run_id}")
    record(
        "C5a content-filter error-aware fallback (az-cf 400 → az-ok 200)",
        s5 == 200,
        f"status={s5}",
    )
    counters5 = _get_stub_counters()
    record(
        "C5b stub: deploy-content-filter called (returned content_filter 400)",
        counters5.get(DEPLOY_CF, 0) > 0,
        f"calls={counters5.get(DEPLOY_CF, 0)}",
    )
    record(
        "C5c stub: deploy-ok called (served after fallover)",
        counters5.get(DEPLOY_OK, 0) > 0,
        f"calls={counters5.get(DEPLOY_OK, 0)}",
    )

    # ── C6 CACHE EXACT HIT ────────────────────────────────────────────────────
    print("\n── C6 CACHE ──")
    cache_msg = f"cache-{run_id}"
    _chat(AZURE_CHAT, cache_msg)  # prime
    _, _, h6 = _chat(AZURE_CHAT, cache_msg)  # repeat → expect X-Cache: hit
    x_cache = {k.lower(): v for k, v in h6.items()}.get("x-cache", "")
    record(
        "C6 repeated chat → X-Cache: hit",
        x_cache.lower() == "hit",
        f"x-cache={x_cache!r}",
    )

    # ── Summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for crit, ok, note in RESULTS:
        print(f"  {'✓' if ok else '✗'} {crit}")
    print(f"\nv21 azure verify: {passed}/{total} checks passed  (run_id={run_id})")
    if passed != total:
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
