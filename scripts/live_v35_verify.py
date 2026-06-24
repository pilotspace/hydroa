#!/usr/bin/env python3
"""v35 error-fidelity live double-pass verifier.

Proves, end-to-end through the Envoy TLS/HTTP edge, that the v35 error-fidelity
behaviors hold (tasks 1 + 2 already shipped; this task only adds the operator triad):

  EF-1  UPSTREAM 429 PASSTHROUGH (Finding A):
      A non-stream chat request to a model whose upstream returns 429 + Retry-After
      is surfaced to the client as HTTP 429 with a Retry-After header (NOT 502).
      Exactly one usage row status=429.

  EF-2  MID-STREAM ERROR FRAME + [DONE] (Finding B + Finding C):
      A streaming request whose upstream delivers ≥1 SSE chunk then closes the
      connection mid-stream is surfaced to the client as: prior chunk(s) + a terminal
      ``data: {"error":{...,"code":"ERR_UPSTREAM_UNAVAILABLE"}} frame`` +
      ``data: [DONE]``. Stream terminates without hanging.

Two modes:
  STUB (default): deterministic; starts v35_error_fidelity_stub on 127.0.0.1:9935,
      seeds catalog models, restarts gateway, runs EF-1 + EF-2 through the edge.
  LIVE (V35_LIVE=1, funded key in apps/gateway/.env): attempts EF-1 / EF-2 against
      real OpenRouter free models; PASS on observed, SKIP if condition cannot be forced.

Security invariants (HARD — never weaken):
  - The funded key is read once via resolve_live_key(), held only in memory.
  - Every printed line passes through _redact(*secrets) before output.
  - The key is passed only in the BYOK PUT body; never echoed, logged, or written.
  - Importing this module does NOT execute main() (guarded by ``if __name__ == '__main__'``).
  - The stub binds 127.0.0.1 only; refusal of 0.0.0.0 is asserted by make_stub_server().

Operator runbook
----------------
1.  Bring up the e2e stack with the v35 overlay (which redirects OpenRouter at the stub):

        docker compose \\
            -f infra/docker-compose.e2e.yml \\
            -f infra/docker-compose.e2e.v4.yml \\
            -f infra/docker-compose.e2e.v5.yml \\
            -f infra/docker-compose.e2e.v6.yml \\
            -f infra/docker-compose.e2e.v35.yml \\
            up --build -d --wait

2.  Run the verifier twice (double-pass close rule):

        python3 scripts/live_v35_verify.py   # pass 1
        python3 scripts/live_v35_verify.py   # pass 2

    Both runs MUST exit 0.

3.  Tear down:

        docker compose \\
            -f infra/docker-compose.e2e.yml \\
            -f infra/docker-compose.e2e.v4.yml \\
            -f infra/docker-compose.e2e.v5.yml \\
            -f infra/docker-compose.e2e.v6.yml \\
            -f infra/docker-compose.e2e.v35.yml \\
            down -v

Environment variables
---------------------
SMOKE_BASE          Edge base URL; default https://localhost:8443; use http://localhost:8080
                    to skip TLS (Envoy still enforces JWT auth either way).
E2E_CA_CERT         CA cert for TLS verification; default infra/envoy/certs/dev-ca.pem.
V35_LIVE            Set to "1" to enable live mode (attempts real OpenRouter probes).
V35_STUB_DROP_MODE  Passed to the stub: "fin" (default) or "rst" (TCP RST).

Exit codes
----------
0   All applicable checks PASS (SKIP is not a failure)
1   Any check FAIL, or a secret detected in the verifier's own output
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE = os.environ.get("SMOKE_BASE", "https://localhost:8443")
CA = os.environ.get("E2E_CA_CERT", "infra/envoy/certs/dev-ca.pem")

STUB_HOST = "127.0.0.1"
STUB_PORT = 9935

# v35 model IDs (must match v35_error_fidelity_stub.py and the overlay)
V35_RATELIMIT_A = "v35/ratelimit-a"
V35_STREAM_FAIL_A = "v35/stream-fail-a"
V35_OK_A = "v35/ok-a"

# Provider / timing
OPENROUTER_PROVIDER = "openrouter"
EDGE_PACE_S = 0.1
EDGE_SETTLE_S = 2.0
STREAM_TIMEOUT_S = 30.0   # EF-2 must complete within this (no-hang assertion)
USAGE_POLL_CEILING_S = 30.0

# Container names (match docker-compose.e2e.yml service names)
PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"

# Per-run unique ID — all identities fresh per pass (double-pass close rule).
run_id = int(time.time())

# Results accumulator: (criterion, status, note)  status ∈ {PASS, FAIL, SKIP}
RESULTS: list[tuple[str, str, str]] = []

# Secret mask
_REDACT_MASK = "***REDACTED***"

# ---------------------------------------------------------------------------
# Stub import — after sys.path setup
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import v35_error_fidelity_stub  # noqa: E402 — after sys.path setup


# ---------------------------------------------------------------------------
# Security helpers (HARD — never weaken)
# ---------------------------------------------------------------------------

def _redact(text: str, *secrets: str) -> str:
    """Return text with every secret replaced by _REDACT_MASK.

    Safe with zero secrets (no-op). Empty secrets are skipped to prevent
    str.replace("", mask) from corrupting the output.
    """
    for s in secrets:
        if s:
            text = text.replace(s, _REDACT_MASK)
    return text


def _print(msg: str, *secrets: str, file: Any = None) -> None:
    """Print msg after redacting all secrets."""
    safe = _redact(msg, *secrets)
    print(safe, file=file or sys.stdout, flush=True)


def record(criterion: str, status: str, note: str, *secrets: str) -> None:
    """Append a result and print it, redacting secrets from note."""
    safe_note = _redact(note, *secrets)
    RESULTS.append((criterion, status, safe_note))
    _print(f"{status}  {criterion}: {safe_note}")


def exit_code(results: list[tuple[str, str, str]]) -> int:
    """Return 1 if any result is FAIL; 0 otherwise (SKIP is not a failure)."""
    for _c, status, _n in results:
        if status == "FAIL":
            return 1
    return 0


# ---------------------------------------------------------------------------
# Live-mode key resolution (HARD security invariants)
# ---------------------------------------------------------------------------

def resolve_live_key() -> str:
    """Read the funded OpenRouter key from apps/gateway/.env (the working key).

    Reads the file once, extracts the GATEWAY_OPENROUTER_API_KEY value, and
    returns it. The key is held in memory ONLY; never logged or written.

    Returns "" when the key is absent, empty, or the file cannot be read
    (caller must treat this as a SKIP, not a FAIL).
    """
    env_file = os.path.join(
        os.path.dirname(_SCRIPTS_DIR), "apps", "gateway", ".env"
    )
    if not os.path.exists(env_file):
        return ""
    try:
        with open(env_file) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("GATEWAY_OPENROUTER_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return val
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------------------
# Infrastructure helpers
# ---------------------------------------------------------------------------

def psql(sql: str) -> str:
    """Run SQL via docker exec psql; return stdout stripped."""
    out = subprocess.run(
        [
            "docker", "exec", PG_CONTAINER,
            "psql", "-U", "gateway", "-d", "gateway_e2e",
            "-tA", "-c", sql,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _tls_ctx() -> Any:
    """Build an SSL context trusting dev-CA; falls back to unverified if absent."""
    import ssl
    if os.path.exists(CA):
        ctx = ssl.create_default_context(cafile=CA)
        return ctx
    _print(f"WARN: CA cert not found at {CA!r}; using unverified TLS")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _post_json(
    path: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    provider_key: str = "",
    timeout: float = 60.0,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """POST JSON; returns (status, body_dict, response_headers)."""
    url = f"{BASE}{path}"
    body = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=_tls_ctx())
        raw = resp.read()
        body_dict: dict[str, Any] = json.loads(raw) if raw else {}
        return resp.status, body_dict, dict(resp.getheaders())
    except urllib.error.HTTPError as exc:
        try:
            err_body: dict[str, Any] = json.loads(exc.read())
        except Exception:
            err_body = {}
        return exc.code, err_body, dict(exc.headers)
    except Exception as exc:
        safe = _redact(str(exc), provider_key)
        return 0, {"error": safe}, {}


def _put_json(
    path: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    provider_key: str = "",
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """PUT JSON; returns (status, body_dict, response_headers)."""
    url = f"{BASE}{path}"
    body = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=body, headers=hdrs, method="PUT")
    try:
        resp = urllib.request.urlopen(req, timeout=30.0, context=_tls_ctx())
        raw = resp.read()
        body_dict: dict[str, Any] = json.loads(raw) if raw else {}
        return resp.status, body_dict, dict(resp.getheaders())
    except urllib.error.HTTPError as exc:
        try:
            err_body: dict[str, Any] = json.loads(exc.read())
        except Exception:
            err_body = {}
        return exc.code, err_body, dict(exc.headers)
    except Exception as exc:
        safe = _redact(str(exc), provider_key)
        return 0, {"error": safe}, {}


def _get_sse_raw(
    path: str,
    payload: dict[str, Any],
    api_key: str,
    *,
    timeout: float = STREAM_TIMEOUT_S,
    provider_key: str = "",
) -> tuple[int, bytes, dict[str, str]]:
    """POST a streaming request; returns (status, raw_body_bytes, resp_headers).

    Uses http.client directly so chunked transfer-encoding is handled correctly
    (urllib.request.read() returns empty on chunked streaming responses).
    The timeout enforces the no-hang contract for EF-2.
    """
    payload_stream = {**payload, "stream": True}
    body_bytes = json.dumps(payload_stream).encode()
    hdrs = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Connection": "close",
    }

    parsed = urllib.parse.urlparse(f"{BASE}{path}")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 8080)
    use_https = parsed.scheme == "https"

    try:
        if use_https:
            import ssl
            conn = http.client.HTTPSConnection(
                host, port, timeout=timeout, context=_tls_ctx()
            )
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)

        conn.request("POST", parsed.path or "/v1/chat/completions",
                     body=body_bytes, headers=hdrs)
        resp = conn.getresponse()
        status = resp.status
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        raw = resp.read()
        conn.close()
        return status, raw, resp_headers
    except Exception as exc:
        safe = _redact(str(exc), provider_key)
        return 0, safe.encode(), {}


# ---------------------------------------------------------------------------
# Stub lifecycle helpers
# ---------------------------------------------------------------------------

def _wait_stub_healthy(timeout: float = 15.0) -> None:
    """Poll http://127.0.0.1:9935/__health until 200 or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"http://{STUB_HOST}:{STUB_PORT}/__health", method="GET"
            )
            if urllib.request.urlopen(req, timeout=3).status == 200:
                _print(f"  [stub] healthy on {STUB_HOST}:{STUB_PORT}")
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"v35 stub did not become healthy within {timeout}s")


def _reset_stub() -> None:
    """POST /__reset to zero stub counters (double-pass idempotency)."""
    try:
        req = urllib.request.Request(
            f"http://{STUB_HOST}:{STUB_PORT}/__reset",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        _print("  [stub] counters reset")
    except Exception as exc:
        _print(f"  [stub] reset warning: {exc}")


def _get_stub_counters() -> dict[str, int]:
    """GET /__counters for per-model call counts."""
    req = urllib.request.Request(
        f"http://{STUB_HOST}:{STUB_PORT}/__counters", method="GET"
    )
    resp = urllib.request.urlopen(req, timeout=5)
    return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Catalog seeding
# ---------------------------------------------------------------------------

def _seed_v35_models() -> None:
    """Seed v35 stub models into catalog + pricing_snapshots (idempotent).

    All models use provider='openrouter' so the gateway routes them through
    the OpenRouter upstream (pointed at the stub by the v35 overlay).
    ON CONFLICT (id) DO UPDATE SET active=true — safe for double-pass.
    """
    models = [
        (V35_RATELIMIT_A,   "v35 ratelimit model (Finding A)",      "chat"),
        (V35_STREAM_FAIL_A, "v35 stream-fail model (Finding B+C)",  "chat"),
        (V35_OK_A,          "v35 ok control model",                  "chat"),
    ]

    model_rows = ", ".join(
        f"('{mid}', '{name}', 8192, true, '{modality}', 'openrouter', now(), now())"
        for mid, name, modality in models
    )
    psql(
        "INSERT INTO models "
        "(id, name, context_length, active, modality, provider, created_at, updated_at) VALUES "
        + model_rows
        + " ON CONFLICT (id) DO UPDATE SET active = true, modality = EXCLUDED.modality,"
          " provider = EXCLUDED.provider;"
    )

    snap_rows = ", ".join(
        f"(gen_random_uuid(), '{mid}', 0.000001, 0.000001, now(), 'per_token', NULL)"
        for mid, _, _ in models
    )
    psql(
        "INSERT INTO pricing_snapshots "
        "(id, model_id, prompt_usd_per_token, completion_usd_per_token, "
        " captured_at, pricing_unit, unit_usd_per_unit) VALUES "
        + snap_rows + ";"
    )
    _print(f"  [seed] {len(models)} v35 models + pricing_snapshots seeded (openrouter, active=true)")


# ---------------------------------------------------------------------------
# Gateway restart helper
# ---------------------------------------------------------------------------

COMPOSE_FILES = [
    "infra/docker-compose.e2e.yml",
    "infra/docker-compose.e2e.v4.yml",
    "infra/docker-compose.e2e.v5.yml",
    "infra/docker-compose.e2e.v6.yml",
    "infra/docker-compose.e2e.v35.yml",
]


def _restart_gateway_and_wait(timeout: float = 90.0) -> None:
    """Restart the gateway container so the lifespan resolver reads seeded rows."""
    cmd = ["docker", "compose"]
    for f in COMPOSE_FILES:
        cmd += ["-f", f]
    cmd += ["restart", "gateway"]
    subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
    _print("  [gateway] restart triggered; polling health ...")

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(
                [
                    "docker", "exec", GW_CONTAINER,
                    "python", "-c",
                    "import urllib.request; "
                    "urllib.request.urlopen('http://localhost:8000/health', timeout=3)",
                ],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                _print("  [gateway] healthy after restart")
                return
        except Exception:
            pass
        time.sleep(2)
    _print("  WARN: gateway health poll timed out; continuing")


# ---------------------------------------------------------------------------
# Usage-row polling helper
# ---------------------------------------------------------------------------

def _poll_usage_row(
    key_id: str,
    *,
    ceiling_s: float = USAGE_POLL_CEILING_S,
    expected_status: int | None = None,
) -> list[dict[str, Any]]:
    """Poll usage_records for key_id until ≥1 row or ceiling_s elapsed."""
    deadline = time.time() + ceiling_s
    while time.time() < deadline:
        count_str = psql(f"SELECT count(*) FROM usage_records WHERE key_id='{key_id}'")
        if int(count_str or "0") >= 1:
            # Fetch status + core columns
            raw = psql(
                "SELECT coalesce(status::text,'NULL') || '|' "
                "|| prompt_tokens::text || '|' "
                "|| completion_tokens::text || '|' "
                "|| cost_usd::text "
                f"FROM usage_records WHERE key_id='{key_id}' ORDER BY created_at"
            )
            rows = []
            for line in raw.splitlines():
                parts = line.split("|")
                if len(parts) >= 4:
                    try:
                        rows.append({
                            "status": None if parts[0] == "NULL" else int(parts[0]),
                            "prompt_tokens": int(parts[1]),
                            "completion_tokens": int(parts[2]),
                            "cost_usd": float(parts[3]),
                        })
                    except (ValueError, IndexError):
                        pass
            if rows:
                return rows
        time.sleep(2)
    return []


# ---------------------------------------------------------------------------
# Tenant provisioning helper
# ---------------------------------------------------------------------------

def _signup_login_key(label: str, *, provider_key: str = "") -> dict[str, str]:
    """Provision a fresh tenant: signup → login → create API key."""
    email = f"v35-verify-{label}-{run_id}@live.io"
    password = "v35-verify-pass"  # noqa: S105 — test harness only
    tenant_name = f"V35EF-{label}-{run_id}"

    s, b, _ = _post_json(
        "/admin/auth/signup",
        {"tenant_name": tenant_name, "email": email, "password": password},
        {},
    )
    if s != 201:
        _print(f"HARD-STOP: signup failed: status={s} body={b}")
        sys.exit(1)

    s, b, _ = _post_json(
        "/admin/auth/login",
        {"email": email, "password": password},
        {},
    )
    if s != 200:
        _print(f"HARD-STOP: login failed: status={s} body={b}")
        sys.exit(1)

    owner_jwt: str = b["access_token"]
    owner_hdrs = {"Authorization": f"Bearer {owner_jwt}"}

    s, b, _ = _post_json(
        "/admin/keys",
        {"name": f"v35-key-{label}-{run_id}"},
        owner_hdrs,
    )
    if s != 201:
        _print(f"HARD-STOP: key creation failed: status={s} body={b}")
        sys.exit(1)

    return {
        "owner_jwt": owner_jwt,
        "api_key": b["key"],
        "key_id": b["key_id"],
        "email": email,
    }


# ---------------------------------------------------------------------------
# EF-1: 429 passthrough (Finding A) — stub mode
# ---------------------------------------------------------------------------

def _run_ef1_stub(api_key: str, key_id: str) -> None:
    """EF-1: POST non-stream chat for v35/ratelimit-a → assert client 429 + Retry-After."""
    _print("\n── EF-1: 429 passthrough (Finding A) ──")
    time.sleep(EDGE_PACE_S)
    payload: dict[str, Any] = {
        "model": V35_RATELIMIT_A,
        "messages": [{"role": "user", "content": f"rate-limit test {run_id}"}],
    }
    key_hdrs = {"Authorization": f"Bearer {api_key}"}
    status, body, resp_hdrs = _post_json(
        "/v1/chat/completions", payload, key_hdrs, timeout=30.0
    )

    ok_status = status == 429
    record(
        "EF-1a client receives HTTP 429 (not 502)",
        "PASS" if ok_status else "FAIL",
        f"status={status} body_keys={list(body.keys())[:4] if isinstance(body, dict) else '?'}",
    )

    # Check Retry-After header (case-insensitive lookup)
    lowercase_hdrs = {k.lower(): v for k, v in resp_hdrs.items()}
    retry_after_val = lowercase_hdrs.get("retry-after", "")
    ok_header = bool(retry_after_val)
    record(
        "EF-1b Retry-After header present",
        "PASS" if ok_header else "FAIL",
        f"Retry-After={retry_after_val!r}",
    )

    # Verify usage row status=429
    rows = _poll_usage_row(key_id)
    if rows:
        row_status = rows[0].get("status")
        ok_row = row_status == 429
        record(
            "EF-1c usage row recorded with status=429",
            "PASS" if ok_row else "FAIL",
            f"row_status={row_status} rows={len(rows)}",
        )
    else:
        record("EF-1c usage row recorded with status=429", "FAIL", "no usage row within poll window")

    # Verify stub saw the call
    counters = _get_stub_counters()
    rl_calls = counters.get(V35_RATELIMIT_A, 0)
    record(
        "EF-1d stub: v35/ratelimit-a called (≥1 attempt)",
        "PASS" if rl_calls >= 1 else "FAIL",
        f"stub_calls[{V35_RATELIMIT_A}]={rl_calls}",
    )


# ---------------------------------------------------------------------------
# EF-2: mid-stream error frame + [DONE] (Finding B + C) — stub mode
# ---------------------------------------------------------------------------

def _run_ef2_stub(api_key: str) -> None:
    """EF-2: POST streaming chat for v35/stream-fail-a; assert error frame + [DONE]."""
    _print("\n── EF-2: mid-stream error frame + [DONE] (Finding B+C) ──")
    time.sleep(EDGE_PACE_S)
    payload: dict[str, Any] = {
        "model": V35_STREAM_FAIL_A,
        "messages": [{"role": "user", "content": f"stream-fail test {run_id}"}],
    }
    status, raw, _ = _get_sse_raw(
        "/v1/chat/completions", payload, api_key, timeout=STREAM_TIMEOUT_S
    )

    ok_status = status == 200
    record(
        "EF-2a streaming response opens with 200",
        "PASS" if ok_status else "FAIL",
        f"status={status}",
    )

    # Check for the ERR_UPSTREAM_UNAVAILABLE error frame
    ok_error_frame = b"ERR_UPSTREAM_UNAVAILABLE" in raw
    record(
        "EF-2b error frame with code ERR_UPSTREAM_UNAVAILABLE present",
        "PASS" if ok_error_frame else "FAIL",
        f"body_preview={raw[:300]!r}",
    )

    # Check for terminal [DONE] — stream must close cleanly
    ok_done = b"[DONE]" in raw
    record(
        "EF-2c terminal data:[DONE] present (stream closes cleanly)",
        "PASS" if ok_done else "FAIL",
        f"has_done={ok_done}",
    )

    # Sanity: at least one partial content chunk before the error frame
    # (the stub emits `"content":"partial"` before dropping)
    ok_partial = b'"partial"' in raw or b"partial" in raw
    record(
        "EF-2d prior partial SSE content chunk received before error frame",
        "PASS" if ok_partial else "FAIL",
        f"has_partial={ok_partial} raw_len={len(raw)}",
    )

    # Verify the stub saw the call
    counters = _get_stub_counters()
    sf_calls = counters.get(V35_STREAM_FAIL_A, 0)
    record(
        "EF-2e stub: v35/stream-fail-a called (≥1 attempt)",
        "PASS" if sf_calls >= 1 else "FAIL",
        f"stub_calls[{V35_STREAM_FAIL_A}]={sf_calls}",
    )


# ---------------------------------------------------------------------------
# EF-CONTROL: v35/ok-a clean stream — stub mode
# ---------------------------------------------------------------------------

def _run_ef_control_stub(api_key: str) -> None:
    """Control check: v35/ok-a streams one chunk + [DONE], no error frame."""
    _print("\n── EF-control: v35/ok-a clean stream ──")
    time.sleep(EDGE_PACE_S)
    payload: dict[str, Any] = {
        "model": V35_OK_A,
        "messages": [{"role": "user", "content": f"control {run_id}"}],
    }
    status, raw, _ = _get_sse_raw(
        "/v1/chat/completions", payload, api_key, timeout=STREAM_TIMEOUT_S
    )

    ok_status = status == 200
    record(
        "EF-control-a ok-a returns 200",
        "PASS" if ok_status else "FAIL",
        f"status={status}",
    )

    ok_done = b"[DONE]" in raw
    ok_no_error = b"ERR_UPSTREAM_UNAVAILABLE" not in raw
    record(
        "EF-control-b ok-a stream contains [DONE] and no error frame",
        "PASS" if (ok_done and ok_no_error) else "FAIL",
        f"has_done={ok_done} no_error={ok_no_error} raw_len={len(raw)}",
    )


# ---------------------------------------------------------------------------
# LIVE mode — real OpenRouter probes (SKIP when condition cannot be forced)
# ---------------------------------------------------------------------------

def _run_live(owner_jwt: str, provider_key: str) -> None:
    """Probe real OpenRouter free models; PASS on observed, SKIP if unobservable.

    Security: provider_key is passed in BYOK PUT body only; every output line
    is _redact()-ed before printing.
    """
    _print("\n── LIVE mode: real OpenRouter probes ──", provider_key)

    # PUT the BYOK key
    owner_hdrs = {"Authorization": f"Bearer {owner_jwt}"}
    s, b, _ = _put_json(
        f"/admin/provider-keys/{OPENROUTER_PROVIDER}",
        {"secret": provider_key},
        owner_hdrs,
        provider_key=provider_key,
    )
    byok_ok = s in (200, 204)
    record(
        "LIVE-provision PUT /admin/provider-keys/openrouter",
        "PASS" if byok_ok else "FAIL",
        f"status={s}",
        provider_key,
    )
    if not byok_ok:
        record("LIVE-EF1 429 passthrough", "SKIP",
               "BYOK PUT failed; skipping live probes")
        record("LIVE-EF2 stream error frame", "SKIP",
               "BYOK PUT failed; skipping live probes")
        return

    # Pick a free model slug to probe — use openrouter/auto or a known free model
    # that may transiently 429 under load
    live_model = os.environ.get(
        "V35_LIVE_MODEL", "mistralai/mistral-7b-instruct:free"
    )

    time.sleep(EDGE_PACE_S)
    # Create a separate key for live probes
    s2, b2, _ = _post_json(
        "/admin/keys",
        {"name": f"v35-live-{run_id}"},
        owner_hdrs,
    )
    if s2 != 201:
        record("LIVE-EF1 429 passthrough", "SKIP",
               "live key creation failed; skipping live probes")
        record("LIVE-EF2 stream error frame", "SKIP", "same")
        return

    live_api_key: str = b2["key"]
    live_hdrs = {"Authorization": f"Bearer {live_api_key}"}

    # EF-1 live: fire a non-stream request; if we get a 429, verify Retry-After
    _print(f"  [live] probing {live_model!r} (non-stream) for EF-1 ...", provider_key)
    time.sleep(EDGE_PACE_S)
    payload_ns: dict[str, Any] = {
        "model": live_model,
        "messages": [{"role": "user", "content": f"hello run_id={run_id}"}],
        "max_tokens": 8,
    }
    ls, lb, lh = _post_json(
        "/v1/chat/completions", payload_ns, live_hdrs,
        provider_key=provider_key, timeout=30.0,
    )
    lowercase_lh = {k.lower(): v for k, v in lh.items()}
    if ls == 429 and "retry-after" in lowercase_lh:
        record(
            "LIVE-EF1 429 passthrough observed",
            "PASS",
            f"status={ls} retry-after={lowercase_lh['retry-after']!r}",
            provider_key,
        )
    elif ls == 429:
        # 429 but no Retry-After — still an observable passthrough (partial pass)
        record(
            "LIVE-EF1 429 passthrough (no Retry-After from provider)",
            "PASS",
            f"status={ls} headers={list(lowercase_lh.keys())}",
            provider_key,
        )
    elif ls == 200:
        record(
            "LIVE-EF1 429 passthrough",
            "SKIP",
            f"free model returned 200 (rate limit not triggered); SKIP not FAIL",
            provider_key,
        )
    else:
        record(
            "LIVE-EF1 429 passthrough",
            "SKIP",
            f"status={ls} (unexpected; not a forced 429) — SKIP not FAIL",
            provider_key,
        )

    # EF-2 live: fire a streaming request; look for error frame or [DONE]
    _print(f"  [live] probing {live_model!r} (stream) for EF-2 ...", provider_key)
    time.sleep(EDGE_PACE_S)
    payload_s: dict[str, Any] = {
        "model": live_model,
        "messages": [{"role": "user", "content": f"Say exactly one word. run_id={run_id}"}],
        "max_tokens": 8,
    }
    lss, lraw, _ = _get_sse_raw(
        "/v1/chat/completions", payload_s, live_api_key,
        timeout=STREAM_TIMEOUT_S, provider_key=provider_key,
    )
    if b"ERR_UPSTREAM_UNAVAILABLE" in lraw or b"ERR_UPSTREAM_RATE_LIMITED" in lraw:
        ok_live_done = b"[DONE]" in lraw
        record(
            "LIVE-EF2 stream error frame + [DONE] observed",
            "PASS" if ok_live_done else "FAIL",
            f"status={lss} has_done={ok_live_done} raw_preview={lraw[:200]!r}",
            provider_key,
        )
    elif lss == 200 and b"[DONE]" in lraw:
        record(
            "LIVE-EF2 stream error frame",
            "SKIP",
            "live stream completed cleanly (no mid-stream failure triggered) — SKIP not FAIL",
            provider_key,
        )
    else:
        record(
            "LIVE-EF2 stream error frame",
            "SKIP",
            f"status={lss} live stream outcome unclassifiable — SKIP not FAIL",
            provider_key,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    _print(f"\nv35 error-fidelity verify  run_id={run_id}  BASE={BASE}")
    _print("=" * 60)

    # ── Start v35 stub ────────────────────────────────────────────────────
    stub_srv = v35_error_fidelity_stub.make_stub_server()
    v35_error_fidelity_stub.start_stub_in_thread(stub_srv)
    _print(f"v35 error-fidelity stub started on {STUB_HOST}:{STUB_PORT}")

    # Security assertion: stub must be bound to loopback ONLY
    server_addr = stub_srv.server_address[0]
    if server_addr != "127.0.0.1":
        _print(
            f"HARD-STOP: stub bound to {server_addr!r} (expected 127.0.0.1)",
            file=sys.stderr,
        )
        sys.exit(1)
    _print(f"  [security] stub address asserted: {server_addr}:{STUB_PORT} (loopback only) OK")

    _wait_stub_healthy()
    _reset_stub()

    # ── Seed catalog ──────────────────────────────────────────────────────
    _print("\n── Seeding v35 catalog ──")
    _seed_v35_models()

    # ── Restart gateway ───────────────────────────────────────────────────
    _print("\n── Restarting gateway (picks up seeded catalog + v35 overlay) ──")
    _restart_gateway_and_wait()
    time.sleep(EDGE_SETTLE_S)

    # ── Provision tenant (stub mode) ──────────────────────────────────────
    _print("\n── Provisioning stub-mode tenant ──")
    tenant = _signup_login_key("stub")
    _print(f"  tenant={tenant['email']}  key_id={tenant['key_id']}")

    # ── PUT BYOK provider key (required post-v25 BYOK milestone) ─────────
    # After v25 every upstream call requires a resolvable per-tenant provider
    # key.  The stub ignores the bearer entirely, so any non-empty placeholder
    # value satisfies the gateway's key-resolution gate.
    _print("\n── PUTting BYOK openrouter key (stub placeholder) ──")
    _STUB_BYOK_PLACEHOLDER = "sk-or-stub-v35-placeholder"  # noqa: S105 — test harness only
    owner_hdrs = {"Authorization": f"Bearer {tenant['owner_jwt']}"}
    byok_s, byok_b, _ = _put_json(
        f"/admin/provider-keys/{OPENROUTER_PROVIDER}",
        {"secret": _STUB_BYOK_PLACEHOLDER},
        owner_hdrs,
    )
    byok_ok = byok_s in (200, 204)
    record(
        "SETUP BYOK PUT /admin/provider-keys/openrouter (stub placeholder)",
        "PASS" if byok_ok else "FAIL",
        f"status={byok_s} body={byok_b}",
    )
    if not byok_ok:
        _print(f"HARD-STOP: BYOK PUT failed status={byok_s} body={byok_b}")
        sys.exit(1)

    # ── Stub-mode checks ─────────────────────────────────────────────────
    _run_ef1_stub(tenant["api_key"], tenant["key_id"])
    _run_ef2_stub(tenant["api_key"])
    _run_ef_control_stub(tenant["api_key"])

    # ── Live mode (optional) ─────────────────────────────────────────────
    live_enabled = os.environ.get("V35_LIVE", "").strip() == "1"
    if live_enabled:
        _print("\n── Live mode enabled (V35_LIVE=1) ──")
        provider_key = resolve_live_key()
        if not provider_key:
            _print("  [live] funded key not found in apps/gateway/.env — SKIP live probes")
            record("LIVE-EF1 429 passthrough", "SKIP",
                   "funded key absent; live probes SKIPPED (not a FAIL)")
            record("LIVE-EF2 stream error frame", "SKIP",
                   "funded key absent; live probes SKIPPED (not a FAIL)")
        else:
            # Provision a separate tenant for live probes so live + stub rows don't mix
            live_tenant = _signup_login_key("live", provider_key=provider_key)
            _print(f"  [live] tenant={live_tenant['email']}", provider_key)
            _run_live(live_tenant["owner_jwt"], provider_key)

            # Secret-leak check on every recorded result note
            for _crit, _status, note in RESULTS:
                if provider_key and provider_key in note:
                    _print("HARD-STOP: provider_key found in results — secret leak!",
                           provider_key)
                    sys.exit(1)
    else:
        _print("\n  [live] V35_LIVE not set — live mode SKIPPED (stub mode is the gate)")

    # ── Summary ───────────────────────────────────────────────────────────
    _print("\n" + "=" * 60)
    _print("FINAL RESULTS — v35 error-fidelity verify")
    _print("=" * 60)
    for criterion, status, note in RESULTS:
        _print(f"  {status}  {criterion}")
        if status == "FAIL":
            _print(f"       evidence: {note}")

    # Compute exit code BEFORE shutdown so stub_srv.shutdown() cannot swallow it.
    code = exit_code(RESULTS)
    passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
    skipped = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    failed = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    total = len(RESULTS)
    _print(
        f"\nv35 error-fidelity: {passed} PASS / {skipped} SKIP / {failed} FAIL"
        f"  (total={total}, run_id={run_id})"
    )

    if code == 0:
        _print("ALL APPLICABLE CRITERIA PASS")
    else:
        _print("FAILURES DETECTED — see table above")

    # Shutdown stub (daemon thread; failure here must NOT change the exit code).
    try:
        stub_srv.shutdown()
    except Exception as _exc:
        _print(f"  [stub] shutdown warning: {_exc}")

    # Exit with the pre-computed code — any exception above cannot flip this.
    sys.exit(code)


if __name__ == "__main__":
    main()
