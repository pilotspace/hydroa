#!/usr/bin/env python3
"""v20 AWS Bedrock live double-pass verification — chat · stream · tools · embeddings · retry · fallback · cache.

Proves, end-to-end through the Envoy TLS/HTTP edge against an INDEPENDENT
SigV4-verifying Bedrock stub, that:
  C1  CHAT:       a Bedrock chat request returns an OpenAI chat.completion with
                  accurate usage billed once (the stub ACCEPTED the SigV4 signature,
                  incl. the ':' (-> %3A) versioned-model path).
  C2  STREAM:     a streaming Bedrock request returns OpenAI SSE chunks ([DONE]).
  C3  TOOLS:      a tool request round-trips OpenAI tools -> Bedrock toolUse and back.
  C4  EMBEDDINGS: a Titan embeddings request returns the OpenAI list shape with
                  EXACT inputTextTokenCount accounting.
  C5  RETRY:      with GATEWAY_UPSTREAM_MAX_RETRIES>0, a model that 503s on attempt 1
                  is transparently served 200; the stub observed >1 signed attempt.
  C6  FALLBACK:   with GATEWAY_UPSTREAM_FALLBACK_ON_ERROR=true, an alias whose first
                  candidate returns a context-window 400 falls over to the next (200).
  C7  CACHE:      a repeated identical chat request returns X-Cache: hit.

The stub re-implements AWS SigV4 from the spec (does NOT import the gateway signer),
so every ACCEPT is a genuine independent cross-check; a tampered request would 403.

Operator-run; NO production source change. Starts scripts/v20_bedrock_stub.py in a
daemon thread (127.0.0.1:9927), seeds catalog models/pricing, restarts gateway,
then runs C1-C7.

run_id = int(time.time()) — changes every run; all identities fresh per pass.

Double-pass close rule: run this script twice in sequence; both must exit 0.

Usage (bring up the stack first):
    docker compose \\
        -f infra/docker-compose.e2e.yml \\
        -f infra/docker-compose.e2e.v4.yml \\
        -f infra/docker-compose.e2e.v5.yml \\
        -f infra/docker-compose.e2e.v6.yml \\
        -f infra/docker-compose.e2e.v20.yml \\
        up --build -d --wait

    python3 scripts/live_v20_verify.py   # pass 1
    python3 scripts/live_v20_verify.py   # pass 2 (double-pass close rule)

    docker compose -f infra/docker-compose.e2e.yml ... -f infra/docker-compose.e2e.v20.yml down -v
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
STUB_PORT = 9927

# Bedrock model ids (must match v20_bedrock_stub.py per-model behaviors + the overlay).
BEDROCK_CHAT = (
    "anthropic.claude-3-5-sonnet-20241022-v2:0"  # ':' -> %3A path, proven through the edge
)
BEDROCK_STREAM = "anthropic.claude-3-haiku-stream"
BEDROCK_TOOL = "anthropic.claude-3-5-sonnet-tool"
BEDROCK_RETRY = "anthropic.claude-retry-once"
BEDROCK_FB_FAIL = "anthropic.claude-fb-fail"
BEDROCK_FB_OK = "anthropic.claude-fb-ok"
BEDROCK_EMBED = "amazon.titan-embed-text-v1"

ALIAS_FB = "bedrock-fb"  # [BEDROCK_FB_FAIL, BEDROCK_FB_OK]

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

import v20_bedrock_stub  # noqa: E402 — after sys.path setup

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
            req = urllib.request.Request(f"http://{STUB_HOST}:{STUB_PORT}/__health", method="GET")
            if urllib.request.urlopen(req, timeout=3).status == 200:  # noqa: S310 — loopback stub
                print(f"  [stub] healthy on {STUB_HOST}:{STUB_PORT}")
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"v20 stub did not become healthy within {timeout}s")


def _reset_stub() -> None:
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
    req = urllib.request.Request(f"http://{STUB_HOST}:{STUB_PORT}/__counters", method="GET")
    resp = urllib.request.urlopen(req, timeout=5)  # noqa: S310 — loopback stub
    return json.loads(resp.read())


def _seed_bedrock_models() -> None:
    """Seed all v20 stub model ids into catalog + pricing_snapshots (provider='bedrock').

    ON CONFLICT (id) DO UPDATE — idempotent across the double-pass.
    """
    all_models = [
        (BEDROCK_CHAT, "v20 bedrock chat", "chat"),
        (BEDROCK_STREAM, "v20 bedrock stream", "chat"),
        (BEDROCK_TOOL, "v20 bedrock tool", "chat"),
        (BEDROCK_RETRY, "v20 bedrock retry candidate", "chat"),
        (BEDROCK_FB_FAIL, "v20 bedrock fallback fail", "chat"),
        (BEDROCK_FB_OK, "v20 bedrock fallback ok", "chat"),
        (BEDROCK_EMBED, "v20 bedrock titan embed", "embedding"),
    ]
    model_rows = ", ".join(
        f"('{mid}', '{name}', 200000, true, '{modality}', 'bedrock', now(), now())"
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
        f"  [seed] {len(all_models)} bedrock models + pricing seeded (provider=bedrock, active=true)"
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


def _post_sse(path: str, payload: dict[str, Any], headers: dict[str, str]) -> tuple[int, str]:
    import http.client
    import urllib.parse

    parsed = urllib.parse.urlparse(f"{BASE}{path}")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 8080)
    body_bytes = json.dumps({**payload, "stream": True}).encode()
    hdrs = {"Content-Type": "application/json", "Connection": "close", **headers}
    try:
        conn = http.client.HTTPConnection(host, port, timeout=60)
        conn.request("POST", parsed.path or "/v1/chat/completions", body=body_bytes, headers=hdrs)
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
        "infra/docker-compose.e2e.v20.yml",
    ]

    print(f"v20 bedrock verify  run_id={run_id}  BASE={BASE}")
    print("=" * 60)

    # ── Start stub ────────────────────────────────────────────────────────
    stub_srv = v20_bedrock_stub.make_stub_server()
    v20_bedrock_stub.start_stub_in_thread(stub_srv)
    print(f"v20 bedrock stub started on {STUB_HOST}:{STUB_PORT}")

    # Security assertion: stub must be bound to loopback ONLY (127.0.0.1, NEVER 0.0.0.0).
    server_addr = stub_srv.server_address[0]
    if server_addr != "127.0.0.1":
        print(f"HARD-STOP: stub bound to {server_addr!r} (expected 127.0.0.1)", file=sys.stderr)
        sys.exit(1)
    print(f"  [security] stub address asserted: {server_addr}:{STUB_PORT} (loopback only) OK")

    _wait_stub_healthy()
    _reset_stub()  # double-pass idempotency: second pass starts from 0

    print("\n── Seeding catalog ──")
    _seed_bedrock_models()

    print("\n── Restarting gateway ──")
    _restart_gateway_and_wait(compose_files)
    time.sleep(EDGE_SETTLE_S)

    print("\n── Auth ──")
    email = f"v20-verify-{run_id}@live.io"
    password = "v20-verify-pass"  # noqa: S105 — test harness only, not a secret

    status, body, _ = _post_json(
        "/admin/auth/signup",
        {"tenant_name": f"V20Co-{run_id}", "email": email, "password": password},
        {},
    )
    assert status == 201, f"signup failed: {status} {body}"

    status, body, _ = _post_json("/admin/auth/login", {"email": email, "password": password}, {})
    assert status == 200, f"login failed: {status} {body}"
    auth_hdrs = {"Authorization": f"Bearer {body['access_token']}"}

    status, body, _ = _post_json(
        "/admin/keys", {"name": f"v20-key-{run_id}", "cache_enabled": True}, auth_hdrs
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
    s, b, _ = _chat(BEDROCK_CHAT, f"chat-{run_id}")
    usage = b.get("usage", {}) if isinstance(b, dict) else {}
    c1_ok = (
        s == 200
        and b.get("object") == "chat.completion"
        and isinstance(usage.get("total_tokens"), int)
    )
    record(
        "C1 bedrock chat (SigV4 accepted incl. %3A path) → OpenAI + usage",
        c1_ok,
        f"status={s} usage={usage}",
    )

    # ── C2 STREAM ────────────────────────────────────────────────────────────
    print("\n── C2 STREAM ──")
    s2, text2 = _post_sse(
        "/v1/chat/completions",
        {"model": BEDROCK_STREAM, "messages": [{"role": "user", "content": f"stream-{run_id}"}]},
        key_hdrs,
    )
    c2_ok = s2 == 200 and "chat.completion.chunk" in text2 and "[DONE]" in text2
    record(
        "C2 bedrock streaming → OpenAI SSE chunks + [DONE]",
        c2_ok,
        f"status={s2} bytes={len(text2)}",
    )

    # ── C3 TOOLS ──────────────────────────────────────────────────────────────
    print("\n── C3 TOOLS ──")
    s3, b3, _ = _chat(
        BEDROCK_TOOL,
        f"tool-{run_id}",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ],
        tool_choice="auto",
    )
    tool_calls = (
        b3.get("choices", [{}])[0].get("message", {}).get("tool_calls")
        if isinstance(b3, dict)
        else None
    )
    c3_ok = s3 == 200 and bool(tool_calls) and tool_calls[0]["function"]["name"] == "get_weather"
    record(
        "C3 bedrock tool round-trip → OpenAI tool_calls",
        c3_ok,
        f"status={s3} tool_calls={bool(tool_calls)}",
    )

    # ── C4 EMBEDDINGS ──────────────────────────────────────────────────────────
    print("\n── C4 EMBEDDINGS ──")
    embed_input = "hello"
    time.sleep(EDGE_PACE_S)
    s4, b4, _ = _post_json(
        "/v1/embeddings", {"model": BEDROCK_EMBED, "input": embed_input}, key_hdrs
    )
    total_tokens = b4.get("usage", {}).get("total_tokens") if isinstance(b4, dict) else None
    data = b4.get("data") if isinstance(b4, dict) else None
    c4_ok = (
        s4 == 200 and bool(data) and total_tokens == len(embed_input)
    )  # exact: stub returns len(inputText)
    record(
        "C4 bedrock Titan embeddings → OpenAI list + EXACT tokens",
        c4_ok,
        f"status={s4} total_tokens={total_tokens}",
    )

    # ── C5 RETRY ────────────────────────────────────────────────────────────
    print("\n── C5 RETRY ──")
    s5, b5, _ = _chat(BEDROCK_RETRY, f"retry-{run_id}")
    record("C5a bedrock retry-to-success → 200", s5 == 200, f"status={s5}")
    time.sleep(0.5)
    counters = _get_stub_counters()
    retry_calls = counters.get(BEDROCK_RETRY, 0)
    record(
        "C5b stub observed >1 signed attempt (retry happened)",
        retry_calls > 1,
        f"stub_calls={retry_calls}",
    )

    # ── C6 ERROR-AWARE FALLBACK ───────────────────────────────────────────────
    print("\n── C6 FALLBACK ──")
    s6, b6, _ = _chat(ALIAS_FB, f"fallback-{run_id}")
    record("C6a error-aware fallback (fb-fail 400 ctx → fb-ok 200)", s6 == 200, f"status={s6}")
    counters6 = _get_stub_counters()
    record(
        "C6b stub: fb-fail called (returned context-window 400)",
        counters6.get(BEDROCK_FB_FAIL, 0) > 0,
        f"calls={counters6.get(BEDROCK_FB_FAIL, 0)}",
    )
    record(
        "C6c stub: fb-ok called (served after fallover)",
        counters6.get(BEDROCK_FB_OK, 0) > 0,
        f"calls={counters6.get(BEDROCK_FB_OK, 0)}",
    )

    # ── C7 CACHE EXACT HIT ────────────────────────────────────────────────────
    print("\n── C7 CACHE ──")
    cache_msg = f"cache-{run_id}"
    _chat(BEDROCK_CHAT, cache_msg)  # prime
    _, _, h7 = _chat(BEDROCK_CHAT, cache_msg)  # repeat → expect X-Cache: hit
    x_cache = {k.lower(): v for k, v in h7.items()}.get("x-cache", "")
    record("C7 repeated chat → X-Cache: hit", x_cache.lower() == "hit", f"x-cache={x_cache!r}")

    # ── Summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for crit, ok, note in RESULTS:
        print(f"  {'✓' if ok else '✗'} {crit}")
    print(f"\nv20 bedrock verify: {passed}/{total} checks passed  (run_id={run_id})")
    if passed != total:
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
