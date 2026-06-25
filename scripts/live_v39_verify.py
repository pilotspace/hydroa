#!/usr/bin/env python3
"""v39 agent-oauth-harness-e2e live double-pass — full headless-agent journey through Envoy.

Proves the v39 milestone end-to-end: a freshly signed-up tenant's user approves a device code,
the agent mints a token, and calls /v1/chat/completions through Envoy HTTP (:8080); the
response is 200 and the agent token was authenticated at /internal/authz (the edge gate
this task widens — previously sk- only, now also accepts agent tokens).

Journey (through Envoy :8080, then gateway :8000 for /internal/* which Envoy blocks):
  A1  POST /admin/auth/signup   — create tenant T
  A2  POST /admin/auth/login    — get owner JWT
  A2b POST /admin/keys          — create an API key for regression test A8
  A3  POST /oauth/device/authorize — agent starts device flow; get device_code + user_code
  A4  POST /oauth/device/approve (JWT) — user approves the user_code
  A5  POST /oauth/token         — agent polls and mints access_token
  A5b DB lookup                 — look up the minted token_id (not in token response by design)
  A6  POST /v1/chat/completions (agent token) — agent makes a billable request → 200
  A7  POST /internal/authz/v1/chat/completions (agent token, via docker exec) → 200 + headers
  A8  POST /internal/authz/v1/chat/completions (sk- key, via docker exec)     → 200 + headers
  A9  POST /internal/authz/v1/chat/completions (unknown token, via docker exec) → 401 fail-closed
  A10 stub counter cross-check — confirms at least one chat call reached the stub

Upstream stub:
  v39_upstream_stub.py runs as a Docker Compose sidecar (service: stub) on :9939
  within the compose network. The gateway reaches it at http://stub:9939/api/v1.
  This script polls the stub's published port (localhost:9939) for counters.

The live double-pass proves the REAL edge path (Envoy ext_authz → /internal/authz → gateway)
authenticates agent tokens — the gap that the stubbed-only suite in task-5 missed.

/internal/authz is externally blocked by Envoy (403 direct response). We call it from
INSIDE the gateway container via `docker exec` + python urllib — exactly what Envoy does.

Double-pass close rule: run this script twice; both must exit 0, no source change between.

Usage (bring up the stack first):
    docker compose \\
        -f infra/docker-compose.e2e.yml \\
        -f infra/docker-compose.e2e.v39.yml \\
        up --build -d --wait

    python3 scripts/live_v39_verify.py   # pass 1
    python3 scripts/live_v39_verify.py   # pass 2 (double-pass close rule)

    docker compose \\
        -f infra/docker-compose.e2e.yml \\
        -f infra/docker-compose.e2e.v39.yml \\
        down -v

Security: stub is a canned-response sidecar (no real credentials); Bearer values
are NEVER logged by the stub.
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

BASE = os.environ.get("SMOKE_BASE", "http://localhost:8080")

# Stub published to localhost:9939 from the compose sidecar (service: stub)
STUB_HOST = "127.0.0.1"
STUB_PORT = 9939

EDGE_PACE_S = 0.2    # pace between requests to avoid rate-limit flaps
EDGE_SETTLE_S = 2.0  # settle time after stack startup

PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"

# Model seeded in catalog — OpenRouter wire shape
MODEL_ID = "or-chat-v39"

# Per-run ID — fresh tenant identity per pass (double-pass idempotency)
run_id = int(time.time())

RESULTS: list[tuple[str, bool, str]] = []

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def record(criterion: str, ok: bool, note: str) -> None:
    RESULTS.append((criterion, ok, note))
    print(f"{'PASS' if ok else 'FAIL'}  {criterion}: {note}", flush=True)


def psql(sql: str) -> str:
    out = subprocess.run(
        [
            "docker", "exec", PG_CONTAINER,
            "psql", "-U", "gateway", "-d", "gateway_e2e", "-tA", "-c", sql,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _http(
    method: str,
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict[str, Any], dict[str, str]]:
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


def _post_json(
    path: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> tuple[int, dict[str, Any], dict[str, str]]:
    hdrs = {"Content-Type": "application/json", **headers}
    return _http("POST", f"{BASE}{path}", json.dumps(payload).encode(), hdrs)


def _wait_stub_healthy(timeout: float = 30.0) -> None:
    """Wait for the stub sidecar to be reachable on its published port."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s, _, _ = _http("GET", f"http://{STUB_HOST}:{STUB_PORT}/__health", timeout=3)
        if s == 200:
            print(f"  [stub] healthy on {STUB_HOST}:{STUB_PORT}")
            return
        time.sleep(0.5)
    raise RuntimeError(f"stub on :{STUB_PORT} did not become healthy within {timeout}s")


def _reset_stub() -> None:
    """POST /__reset to clear stub counters — double-pass idempotency."""
    s, _, _ = _http(
        "POST", f"http://{STUB_HOST}:{STUB_PORT}/__reset",
        b"{}",
        {"Content-Type": "application/json"},
        timeout=5,
    )
    print(f"  [stub :{STUB_PORT}] reset counters (status={s})")


def _stub_counters() -> dict[str, int]:
    _, body, _ = _http("GET", f"http://{STUB_HOST}:{STUB_PORT}/__counters", timeout=5)
    return {k: int(v) for k, v in body.items()} if isinstance(body, dict) else {}


def _seed_models() -> None:
    """Seed one OpenRouter chat model into catalog + pricing. Idempotent."""
    psql(
        f"INSERT INTO models (id, name, context_length, active, modality, provider, created_at, updated_at)"
        f" VALUES ('{MODEL_ID}', 'v39 agent-oauth chat', 200000, true, 'chat', 'openrouter', now(), now())"
        f" ON CONFLICT (id) DO UPDATE SET active = true, modality = 'chat', provider = 'openrouter';"
    )
    psql(
        f"INSERT INTO pricing_snapshots (id, model_id, prompt_usd_per_token,"
        f" completion_usd_per_token, captured_at, pricing_unit, unit_usd_per_unit)"
        f" VALUES (gen_random_uuid(), '{MODEL_ID}', 0.000003, 0.000015, now(), 'per_token', NULL)"
        f" ON CONFLICT DO NOTHING;"
    )
    print(f"  [seed] model {MODEL_ID!r} + pricing seeded (active=true, provider=openrouter)")


def _wait_gateway_healthy(timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(
                [
                    "docker", "exec", GW_CONTAINER,
                    "python", "-c",
                    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)",
                ],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                print("  [gateway] healthy")
                return
        except Exception:
            pass
        time.sleep(2)
    print("  WARN: gateway health poll timed out; continuing")


def _exec_authz(container: str, bearer: str) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Call /internal/authz/v1/chat/completions from inside the gateway container.

    Envoy blocks /internal/* externally (403 direct response). This simulates what
    Envoy's ext_authz does: call the gateway directly on :8000.
    Bearer value is passed as a Python literal — never concatenated into a shell
    string that could be logged.
    """
    result = subprocess.run(
        [
            "docker", "exec", container,
            "python", "-c",
            (
                "import urllib.request, urllib.error, json\n"
                f"bearer = {bearer!r}\n"
                "req = urllib.request.Request(\n"
                "    'http://localhost:8000/internal/authz/v1/chat/completions',\n"
                "    data=b'{}',\n"
                "    headers={'Content-Type': 'application/json', "
                "'Authorization': 'Bearer ' + bearer},\n"
                "    method='POST',\n"
                ")\n"
                "try:\n"
                "    resp = urllib.request.urlopen(req, timeout=10)\n"
                "    headers = dict(resp.getheaders())\n"
                "    body = json.loads(resp.read())\n"
                "    print(json.dumps({'status': resp.status, 'body': body, 'headers': headers}))\n"
                "except urllib.error.HTTPError as e:\n"
                "    body = json.loads(e.read()) if e.fp else {}\n"
                "    print(json.dumps({'status': e.code, 'body': body, 'headers': {}}))\n"
            ),
        ],
        capture_output=True, text=True, timeout=15,
    )
    try:
        inner = json.loads(result.stdout.strip())
        s = int(inner.get("status", 0))
        b = inner.get("body", {})
        # Normalize headers to lowercase for consistent lookup
        raw_h = inner.get("headers", {})
        h = {k.lower(): v for k, v in (raw_h.items() if isinstance(raw_h, dict) else [])}
        return s, b, h
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0, {}, {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"v39 agent-oauth live verify  run_id={run_id}  BASE={BASE}")
    print("=" * 64)

    # ── Confirm stub sidecar is reachable (compose brings it up before gateway) ─
    print("\n── Stub sidecar ──")
    _wait_stub_healthy()
    _reset_stub()

    # ── Seed catalog ─────────────────────────────────────────────────────────
    print("\n── Seeding catalog ──")
    _seed_models()

    # ── Wait for gateway ──────────────────────────────────────────────────────
    print("\n── Checking gateway health ──")
    _wait_gateway_healthy()
    time.sleep(EDGE_SETTLE_S)

    # ── A1: Signup ────────────────────────────────────────────────────────────
    print(f"\n── Journey (run_id={run_id}) ──")
    email = f"v39-{run_id}@live.io"
    password = "v39-verify-pass"  # noqa: S105 — test harness only
    tenant_name = f"V39-Tenant-{run_id}"

    s, b, _ = _post_json("/admin/auth/signup",
                         {"tenant_name": tenant_name, "email": email, "password": password}, {})
    ok_signup = s == 201
    record("A1 signup", ok_signup, f"status={s}")
    if not ok_signup:
        print(f"  body={b}")
        sys.exit(1)
    tenant_id: str = b.get("tenant_id", "")

    # ── A2: Login ────────────────────────────────────────────────────────────
    time.sleep(EDGE_PACE_S)
    s, b, _ = _post_json("/admin/auth/login", {"email": email, "password": password}, {})
    ok_login = s == 200
    record("A2 login", ok_login, f"status={s}")
    if not ok_login:
        sys.exit(1)
    owner_jwt: str = b.get("access_token", "")
    owner_hdrs = {"Authorization": f"Bearer {owner_jwt}"}

    # Create an API key for the regression check (A8)
    time.sleep(EDGE_PACE_S)
    s, b, _ = _post_json("/admin/keys", {"name": f"v39-key-{run_id}"}, owner_hdrs)
    ok_key = s == 201
    record("A2b create API key", ok_key, f"status={s}")
    api_key_str: str = b.get("key", "") if ok_key else ""
    api_key_id: str = b.get("key_id", "") if ok_key else ""

    # Seed per-tenant openrouter provider key so CachedTenantCredentialResolver resolves
    # correctly. The BYOK store always checks the encryption key on get() even before
    # reading a row; without a seeded row it raises ProviderKeyMissing → 402.
    # The stub ignores the Bearer value so "stub-v39-openrouter-key" is the placeholder.
    # PUT /admin/provider-keys/{provider} — upsert returns 200.
    time.sleep(EDGE_PACE_S)
    hdrs_put = {"Content-Type": "application/json", **owner_hdrs}
    s, b, _ = _http(
        "PUT",
        f"{BASE}/admin/provider-keys/openrouter",
        json.dumps({"secret": "stub-v39-openrouter-key"}).encode(),
        hdrs_put,
    )
    ok_provkey = s in (200, 201, 204)
    record("A2c seed openrouter provider key", ok_provkey, f"status={s}")
    if not ok_provkey:
        print(f"  body={b}")
        # Non-fatal: A6 will fail downstream but we continue to report all results.

    # ── A3: Device authorize ─────────────────────────────────────────────────
    time.sleep(EDGE_PACE_S)
    s, b, _ = _post_json("/oauth/device/authorize", {"scope": "proxy"}, {})
    ok_authz = s == 200
    record("A3 device/authorize", ok_authz, f"status={s}")
    if not ok_authz:
        print(f"  body={b}")
        sys.exit(1)
    device_code: str = b.get("device_code", "")
    user_code: str = b.get("user_code", "")

    # ── A4: Approve ──────────────────────────────────────────────────────────
    time.sleep(EDGE_PACE_S)
    s, b, _ = _post_json("/oauth/device/approve",
                         {"user_code": user_code, "approved": True}, owner_hdrs)
    ok_approve = s == 200
    record("A4 device/approve", ok_approve, f"status={s}")
    if not ok_approve:
        print(f"  body={b}")
        sys.exit(1)

    # ── A5: Poll token ────────────────────────────────────────────────────────
    time.sleep(EDGE_PACE_S)
    s, b, _ = _post_json(
        "/oauth/token",
        {"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": device_code},
        {},
    )
    ok_token = s == 200 and "access_token" in (b if isinstance(b, dict) else {})
    record("A5 mint token", ok_token, f"status={s}")
    if not ok_token:
        print(f"  body={b}")
        sys.exit(1)
    access_token: str = b.get("access_token", "") if isinstance(b, dict) else ""
    agent_hdrs = {"Authorization": f"Bearer {access_token}"}

    # A5b: Look up token_id from DB — the token endpoint does NOT return it by design
    # (security: only the plaintext access_token is returned once; id is internal).
    token_id = psql(
        "SELECT id FROM agent_tokens"
        f" WHERE tenant_id = '{tenant_id}'"
        " ORDER BY created_at DESC LIMIT 1;"
    ).strip()
    record(
        "A5b token_id lookup from DB",
        bool(token_id),
        f"token_id={token_id[:8]}..." if token_id else "EMPTY",
    )

    # ── A6: /v1/chat/completions with agent token (through Envoy) ────────────
    time.sleep(EDGE_PACE_S)
    before_chat = _stub_counters()
    s, b, _ = _post_json(
        "/v1/chat/completions",
        {"model": MODEL_ID, "messages": [{"role": "user", "content": f"hello-{run_id}"}]},
        agent_hdrs,
    )
    ok_chat = s == 200 and isinstance(b, dict) and b.get("object") == "chat.completion"
    record(
        "A6 /v1/chat with agent token → 200 chat.completion",
        ok_chat,
        f"status={s} obj={b.get('object') if isinstance(b, dict) else None}",
    )
    if not ok_chat:
        print(f"  body={b}")

    # ── A7: /internal/authz with agent token (via docker exec — simulates Envoy) ─
    # Envoy blocks /internal/* externally. Call from inside the gateway container.
    time.sleep(EDGE_PACE_S)
    s7, b7, h7 = _exec_authz(GW_CONTAINER, access_token)
    ok_authz_agent = (
        s7 == 200
        and h7.get("x-tenant-id", "").lower() == tenant_id.lower()
        and (not token_id or h7.get("x-key-id", "").lower() == token_id.lower())
    )
    record(
        "A7 /internal/authz agent token → 200 + x-tenant-id + x-key-id",
        ok_authz_agent,
        f"status={s7} x-tenant-id={h7.get('x-tenant-id', '<missing>')} "
        f"x-key-id={h7.get('x-key-id', '<missing>')}",
    )
    if not ok_authz_agent:
        print(f"  expected tenant_id={tenant_id} token_id={token_id}")
        print(f"  body={b7}")

    # ── A8: /internal/authz with sk- key — regression check ─────────────────
    time.sleep(EDGE_PACE_S)
    if api_key_str:
        s8, b8, h8 = _exec_authz(GW_CONTAINER, api_key_str)
        ok_sk = s8 == 200 and (
            not api_key_id or h8.get("x-key-id", "").lower() == api_key_id.lower()
        )
        record(
            "A8 /internal/authz sk- regression → 200 + x-key-id",
            ok_sk,
            f"status={s8} x-key-id={h8.get('x-key-id', '<missing>')}",
        )
    else:
        record("A8 /internal/authz sk- regression", False, "SKIP: no API key created")

    # ── A9: /internal/authz fail-closed — unknown token → 401 ────────────────
    time.sleep(EDGE_PACE_S)
    unknown_token = "unknownagenttoken000000000000000000000000000"
    s9, b9, _ = _exec_authz(GW_CONTAINER, unknown_token)
    ok_fail_closed = s9 == 401 and (
        isinstance(b9, dict) and b9.get("code") == "ERR_AUTH_INVALID_KEY"
    )
    record(
        "A9 /internal/authz unknown token → 401 fail-closed",
        ok_fail_closed,
        f"status={s9} code={b9.get('code') if isinstance(b9, dict) else None}",
    )

    # ── A10: stub counter cross-check ─────────────────────────────────────────
    after_chat = _stub_counters()
    delta = after_chat.get("chat", 0) - before_chat.get("chat", 0)
    record("A10 stub ACCEPTED at least 1 chat request", delta >= 1, f"accepts={delta}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for crit, ok, _note in RESULTS:
        print(f"  {'✓' if ok else '✗'} {crit}")
    print(f"\nv39 agent-oauth live verify: {passed}/{total} checks passed  (run_id={run_id})")
    if passed != total:
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
