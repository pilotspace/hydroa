#!/usr/bin/env python3
"""Helios live-integration smoke test — v34 exit criterion (double-pass).

Proves a real OpenAI-wire agent-coding client drives the proxy end-to-end against
a REAL provider (Gemini via gcloud-minted AI Studio key), exercising the v34
hardening: reasoning passthrough, prompt-cache, parallel tool streaming,
disconnect billing, and back-pressure.

Operator runbook
----------------
1.  Enable the Generative Language API and mint a key (run interactively):
        gcloud services enable generativelanguage.googleapis.com
        gcloud services api-keys create --display-name="helios-smoke"
    Export the key value (never persist it):
        export HELIOS_GEMINI_KEY='<minted-key>'

2.  Bring up the e2e TLS + Envoy stack:
        docker compose -f infra/docker-compose.e2e.yml up --build -d --wait

3.  Run the verifier TWICE (double-pass close rule):
        uv run --project apps/gateway python scripts/live_helios_smoke.py
        uv run --project apps/gateway python scripts/live_helios_smoke.py

    Both runs MUST exit 0.  run_id changes per invocation — identities are fresh.

4.  Tear down:
        docker compose -f infra/docker-compose.e2e.yml down -v

Environment variables
---------------------
SMOKE_BASE                  Edge base URL (default: https://localhost:8443)
E2E_CA_CERT                 CA cert for TLS verification (default: infra/envoy/certs/dev-ca.pem)
HELIOS_GEMINI_KEY           REQUIRED — gcloud-minted Generative Language key; absent → exit 2
GATEWAY_MAX_CONCURRENT_REQUESTS
                            Optional; >0 enables C6 back-pressure criterion; absent/0 → C6 SKIPPED

Exit codes
----------
0   All applicable criteria PASS (SKIP is not a failure)
1   Any criterion FAIL or a secret-leak detected in output
2   HELIOS_GEMINI_KEY absent or empty

Security invariants (HARD contract — never weaken)
--------------------------------------------------
- The Gemini key is read ONCE via resolve_gemini_key(), held only in memory.
- Every print/log line is passed through _redact(..., _GEMINI_KEY) before output.
- The key is passed only in the BYOK PUT body; it is never echoed, logged, or written.
- Importing this module does NOT execute main() (guarded by `if __name__ == '__main__':`).
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

BASE = os.environ.get("SMOKE_BASE", "https://localhost:8443")
CA = os.environ.get("E2E_CA_CERT", "infra/envoy/certs/dev-ca.pem")

# Per-run unique ID — embeds in tenant email/key names so each invocation is fresh.
run_id = int(time.time())

# Results accumulator: (criterion, status, note) where status ∈ {PASS, FAIL, SKIP}
RESULTS: list[tuple[str, str, str]] = []

# Helios live smoke Gemini model id (seeded by this script into the live catalog).
HELIOS_GEMINI_MODEL = "helios-smoke-gemini-flash"
HELIOS_GEMINI_PROVIDER = "google"

PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"

EDGE_PACE_S = 0.1
EDGE_SETTLE_S = 3.0

# Secret mask — all log lines go through _redact() before printing.
_REDACT_MASK = "***REDACTED***"

# ---------------------------------------------------------------------------
# Security-critical pure helpers (tested offline)
# ---------------------------------------------------------------------------


def resolve_gemini_key() -> str:
    """Read HELIOS_GEMINI_KEY from env; absent/empty → SystemExit(2).

    The key is held in memory only.  It is NEVER logged, echoed, or written.
    This is the ONLY call-site that reads the env var.
    """
    key = os.environ.get("HELIOS_GEMINI_KEY", "").strip()
    if not key:
        print(
            "ERROR: HELIOS_GEMINI_KEY is absent or empty. "
            "Set the env var to a gcloud-minted Generative Language API key.",
            file=sys.stderr,
        )
        sys.exit(2)
    return key


def _redact(line: str, *secrets: str) -> str:
    """Return *line* with every occurrence of each secret replaced by _REDACT_MASK.

    Safe to call with zero secrets (no-op).  Empty secret strings are skipped so
    they do not corrupt the output via str.replace("", mask).
    """
    for secret in secrets:
        if secret:
            line = line.replace(secret, _REDACT_MASK)
    return line


def exit_code(results: list[tuple[str, str, str]]) -> int:
    """Return the process exit code derived from a list of (criterion, status, note).

    Rules (FROZEN contract):
      - Any status == 'FAIL'  →  return 1
      - 'SKIP' does NOT force non-zero (it means 'not applicable this run')
      - All PASS (or SKIP only) →  return 0
    """
    for _criterion, status, _note in results:
        if status == "FAIL":
            return 1
    return 0


def applicable(criterion: str, env: dict[str, str]) -> str:
    """Return 'SKIP' when the criterion is conditionally gated and the gate is off.

    Currently only C6 is conditional:
      C6 is SKIPPED when GATEWAY_MAX_CONCURRENT_REQUESTS is absent or '0'/''.
      All other criteria are always applicable ('RUN').

    Args:
        criterion: The criterion label, e.g. 'C6'.
        env: Mapping of env var names to values (use os.environ for live runs,
             or a test dict for offline testing).
    Returns:
        'SKIP' if the criterion should be skipped this run; 'RUN' otherwise.
    """
    if criterion == "C6":
        cap_str = env.get("GATEWAY_MAX_CONCURRENT_REQUESTS", "").strip()
        try:
            cap = int(cap_str)
        except (ValueError, TypeError):
            cap = 0
        if cap <= 0:
            return "SKIP"
    return "RUN"


# ---------------------------------------------------------------------------
# Output helper — ALL prints go through this so secrets are never emitted
# ---------------------------------------------------------------------------

def _print(msg: str, *secrets: str, file: Any = None) -> None:
    """Print msg after redacting all secrets.  Default file=sys.stdout."""
    safe = _redact(msg, *secrets)
    print(safe, file=file or sys.stdout, flush=True)


def record(criterion: str, status: str, note: str, *secrets: str) -> None:
    """Append a result and print it, redacting any secrets from note."""
    safe_note = _redact(note, *secrets)
    RESULTS.append((criterion, status, safe_note))
    _print(f"{status}  {criterion}: {safe_note}")


# ---------------------------------------------------------------------------
# Infrastructure helpers (psql / HTTP / stack)
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


def _tls_ctx() -> ssl.SSLContext:
    """Build an SSL context trusting the dev-CA cert; falls back to unverified if cert absent."""
    if os.path.exists(CA):
        ctx = ssl.create_default_context(cafile=CA)
        return ctx
    # No CA cert present — warn and use unverified (operator pre-condition not met)
    _print(f"WARN: CA cert not found at {CA!r}; using unverified TLS (set E2E_CA_CERT)")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _http(
    method: str,
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
    *,
    gemini_key: str = "",
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """HTTP request; returns (status, body_dict, headers).

    The gemini_key param is accepted so call-sites can pass it for redaction in
    error messages; the key itself is never included in the returned data.
    """
    req = urllib.request.Request(
        url, data=data, headers=headers or {}, method=method
    )
    try:
        resp = urllib.request.urlopen(  # noqa: S310 — edge/loopback under test
            req, timeout=timeout, context=_tls_ctx()
        )
        raw = resp.read()
        body: dict[str, Any] = json.loads(raw) if raw else {}
        return resp.status, body, dict(resp.getheaders())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except Exception:
            body = {}
        return exc.code, body, dict(exc.headers)
    except Exception as exc:
        safe_msg = _redact(str(exc), gemini_key) if gemini_key else str(exc)
        return 0, {"error": safe_msg}, {}


def _post_json(
    path: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    gemini_key: str = "",
) -> tuple[int, dict[str, Any], dict[str, str]]:
    hdrs = {"Content-Type": "application/json", **headers}
    return _http(
        "POST", f"{BASE}{path}",
        json.dumps(payload).encode(),
        hdrs,
        gemini_key=gemini_key,
    )


def _put_json(
    path: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    gemini_key: str = "",
) -> tuple[int, dict[str, Any], dict[str, str]]:
    hdrs = {"Content-Type": "application/json", **headers}
    return _http(
        "PUT", f"{BASE}{path}",
        json.dumps(payload).encode(),
        hdrs,
        gemini_key=gemini_key,
    )


def _signup_login_key(label: str) -> dict[str, str]:
    """Provision a fresh tenant: signup → login → create API key.

    Returns dict with 'owner_jwt', 'api_key', 'key_id', 'email'.
    """
    email = f"helios-smoke-{label}-{run_id}@live.io"
    password = "helios-smoke-pass"  # noqa: S105 — test harness only
    tenant_name = f"HeliosSmoke-{label}-{run_id}"

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
        {"name": f"helios-key-{label}-{run_id}"},
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


def _seed_helios_model() -> None:
    """Seed the Helios smoke Gemini model + pricing_snapshots via psql.

    Idempotent (ON CONFLICT DO UPDATE).  Must run BEFORE gateway restart so the
    lifespan provider_resolver.refresh() picks up active=true.

    Pricing: per_token with both prompt + completion > 0 → cost_usd > 0 for C1/C4.
    """
    _ts = "now(), now()"
    model_sql = (
        "INSERT INTO models "
        "(id, name, context_length, active, modality, provider, created_at, updated_at) VALUES "
        f"('{HELIOS_GEMINI_MODEL}', 'Helios Smoke Gemini Flash', 1000000, true, "
        f"'chat', '{HELIOS_GEMINI_PROVIDER}', {_ts}) "
        "ON CONFLICT (id) DO UPDATE SET active = true, "
        "provider = EXCLUDED.provider, modality = EXCLUDED.modality;"
    )
    psql(model_sql)

    # pricing_snapshots has no unique (model_id) — insert fresh each run.
    # cost_calculator uses ORDER BY captured_at DESC (latest snapshot wins).
    snap_sql = (
        "INSERT INTO pricing_snapshots "
        "(id, model_id, prompt_usd_per_token, completion_usd_per_token, "
        "captured_at, pricing_unit, unit_usd_per_unit) VALUES "
        f"(gen_random_uuid(), '{HELIOS_GEMINI_MODEL}', 0.000001, 0.000001, now(), 'per_token', NULL);"
    )
    psql(snap_sql)
    _print(f"  [seed] {HELIOS_GEMINI_MODEL} model + pricing_snapshot seeded (per_token, active=true)")


def _restart_gateway_and_wait(timeout: float = 90.0) -> None:
    """Restart the gateway container; poll /health until ready or timeout."""
    compose_cmd = [
        "docker", "compose",
        "-f", "infra/docker-compose.e2e.yml",
        "restart", "gateway",
    ]
    subprocess.run(compose_cmd, capture_output=True, text=True, check=True, timeout=120)
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
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0:
                _print("  [gateway] healthy after restart")
                return
        except Exception:
            pass
        time.sleep(2)
    _print("  WARN: gateway health poll timed out; continuing")


def _poll_usage_row(
    key_id: str,
    *,
    min_rows: int = 1,
    ceiling_s: float = 30.0,
) -> list[dict[str, Any]]:
    """Poll usage_records for key_id; return list of row dicts when min_rows reached.

    Columns fetched: prompt_tokens, completion_tokens, cost_usd, provider_cost, usage_source.
    Returns empty list if no rows materialise within ceiling_s.
    """
    deadline = time.time() + ceiling_s
    while time.time() < deadline:
        count = int(
            psql(f"SELECT count(*) FROM usage_records WHERE key_id='{key_id}'") or "0"
        )
        if count >= min_rows:
            raw = psql(
                "SELECT prompt_tokens::text || '|' "
                "|| completion_tokens::text || '|' "
                "|| cost_usd::text || '|' "
                "|| coalesce(provider_cost::text, 'NULL') || '|' "
                "|| coalesce(usage_source, 'NULL') "
                f"FROM usage_records WHERE key_id='{key_id}' ORDER BY created_at"
            )
            rows = []
            for line in raw.splitlines():
                parts = line.split("|")
                if len(parts) >= 5:
                    try:
                        rows.append({
                            "prompt_tokens": int(parts[0]),
                            "completion_tokens": int(parts[1]),
                            "cost_usd": float(parts[2]),
                            "provider_cost": None if parts[3] == "NULL" else float(parts[3]),
                            "usage_source": parts[4],
                        })
                    except (ValueError, IndexError):
                        pass
            if rows:
                return rows
        time.sleep(2)
    return []


def _api_key(owner_jwt: str, label: str) -> dict[str, str]:
    """Create a fresh API key under the given owner JWT.  Returns {key, key_id}."""
    owner_hdrs = {"Authorization": f"Bearer {owner_jwt}"}
    s, b, _ = _post_json("/admin/keys", {"name": f"helios-{label}-{run_id}"}, owner_hdrs)
    if s != 201:
        _print(f"HARD-STOP: key {label} creation failed: {s} {b}")
        sys.exit(1)
    return {"key": b["key"], "key_id": b["key_id"]}


def _chat(
    model: str,
    api_key: str,
    *,
    stream: bool = False,
    extra: dict[str, Any] | None = None,
    gemini_key: str = "",
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Send a POST /v1/chat/completions in OpenAI-wire format."""
    time.sleep(EDGE_PACE_S)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": f"Write a one-liner Python hello-world. run_id={run_id}"},
        ],
        "max_tokens": 64,
        "stream": stream,
    }
    if extra:
        payload.update(extra)
    return _post_json(
        "/v1/chat/completions",
        payload,
        {"Authorization": f"Bearer {api_key}"},
        gemini_key=gemini_key,
    )


# ---------------------------------------------------------------------------
# C1 — Coding chat (non-stream): 200 OpenAI shape; 1 row >0/>0/cost>0
# ---------------------------------------------------------------------------


def _run_c1(owner_jwt: str, gemini_key: str) -> None:
    k = _api_key(owner_jwt, "c1")
    s, b, _ = _chat(HELIOS_GEMINI_MODEL, k["key"], gemini_key=gemini_key)

    ok_status = s == 200
    ok_shape = ok_status and isinstance(b, dict) and b.get("object") == "chat.completion"
    record("C1a coding-chat returns 200 OpenAI shape", "PASS" if ok_shape else "FAIL",
           f"status={s} object={b.get('object') if isinstance(b, dict) else None!r}",
           gemini_key)

    rows = _poll_usage_row(k["key_id"])
    if rows:
        r = rows[0]
        ok_tokens = r["prompt_tokens"] > 0 and r["completion_tokens"] > 0
        ok_cost = r["cost_usd"] > 0
        record("C1b usage row: prompt_tokens>0 AND completion_tokens>0",
               "PASS" if ok_tokens else "FAIL",
               f"prompt={r['prompt_tokens']} completion={r['completion_tokens']}")
        record("C1c usage row: cost_usd>0",
               "PASS" if ok_cost else "FAIL",
               f"cost_usd={r['cost_usd']}")
    else:
        record("C1b usage row: prompt_tokens>0 AND completion_tokens>0", "FAIL",
               "no usage row appeared within polling window")
        record("C1c usage row: cost_usd>0", "FAIL", "no usage row")


# ---------------------------------------------------------------------------
# C2 — Streaming parallel tool-calls: SSE tool_call deltas + usage frame; row >0/>0
# ---------------------------------------------------------------------------


def _run_c2(owner_jwt: str, gemini_key: str) -> None:
    k = _api_key(owner_jwt, "c2")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
    ]
    extra: dict[str, Any] = {
        "tools": tools,
        "tool_choice": "auto",
        "messages": [
            {"role": "user",
             "content": f"Read /tmp/foo then write /tmp/bar with 'hello'. run_id={run_id}"},
        ],
    }
    time.sleep(EDGE_PACE_S)
    # For streaming, we use urllib directly to get the raw SSE bytes
    payload: dict[str, Any] = {
        "model": HELIOS_GEMINI_MODEL,
        "stream": True,
        **extra,
    }
    url = f"{BASE}/v1/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {k['key']}",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=60, context=_tls_ctx())  # noqa: S310
        sse_text = resp.read().decode("utf-8", errors="replace")
        c2_status = resp.status
    except urllib.error.HTTPError as exc:
        sse_text = exc.read().decode("utf-8", errors="replace")
        c2_status = exc.code
    except Exception as exc:
        sse_text = ""
        c2_status = 0
        _print(f"  C2 stream error: {_redact(str(exc), gemini_key)}")

    has_tool_call_delta = '"tool_calls"' in sse_text and '"delta"' in sse_text
    has_done = "[DONE]" in sse_text
    # Look for a usage frame in the SSE
    has_usage_frame = False
    for line in sse_text.splitlines():
        if line.startswith("data:") and "[DONE]" not in line:
            raw = line[len("data:"):].strip()
            try:
                chunk = json.loads(raw)
                if chunk.get("usage"):
                    has_usage_frame = True
            except (json.JSONDecodeError, AttributeError):
                pass

    ok_stream = c2_status == 200 and has_done
    record("C2a stream=true returns 200 + [DONE]", "PASS" if ok_stream else "FAIL",
           f"status={c2_status} has_done={has_done}")
    record("C2b SSE carries tool_calls delta", "PASS" if has_tool_call_delta else "FAIL",
           f"has_tool_call_delta={has_tool_call_delta}")
    record("C2c terminal usage frame present in stream", "PASS" if has_usage_frame else "FAIL",
           f"has_usage_frame={has_usage_frame}")

    rows = _poll_usage_row(k["key_id"])
    if rows:
        r = rows[0]
        ok_tokens = r["prompt_tokens"] > 0 and r["completion_tokens"] > 0
        record("C2d usage row reconciles (>0/>0)", "PASS" if ok_tokens else "FAIL",
               f"prompt={r['prompt_tokens']} completion={r['completion_tokens']}")
    else:
        record("C2d usage row reconciles (>0/>0)", "FAIL", "no usage row")


# ---------------------------------------------------------------------------
# C3 — Reasoning passthrough: reasoning_tokens present in response usage (>=0)
# ---------------------------------------------------------------------------


def _run_c3(owner_jwt: str, gemini_key: str) -> None:
    k = _api_key(owner_jwt, "c3")
    extra: dict[str, Any] = {"reasoning_effort": "low"}
    s, b, _ = _chat(HELIOS_GEMINI_MODEL, k["key"], extra=extra, gemini_key=gemini_key)

    ok_status = s == 200
    record("C3a reasoning request returns 200", "PASS" if ok_status else "FAIL",
           f"status={s}")

    if ok_status and isinstance(b, dict):
        usage = b.get("usage", {})
        details = usage.get("completion_tokens_details", {})
        # C3 weakens to "reasoning field present and >=0" per contract (may be 0 if
        # the live model omits thoughtsTokenCount for the chosen effort level)
        reasoning_tokens = details.get("reasoning_tokens")
        ok_reasoning = reasoning_tokens is not None and isinstance(reasoning_tokens, (int, float)) and reasoning_tokens >= 0
        record("C3b reasoning_tokens present in usage.completion_tokens_details (>=0)",
               "PASS" if ok_reasoning else "FAIL",
               f"reasoning_tokens={reasoning_tokens!r}")
    else:
        record("C3b reasoning_tokens present in usage.completion_tokens_details (>=0)",
               "FAIL", f"status={s} body type={type(b).__name__}")


# ---------------------------------------------------------------------------
# C4 — Prompt-cache: 2nd call surfaces cache detail; both rows cost>0
# ---------------------------------------------------------------------------

_LARGE_CONTEXT_PREFIX = (
    "You are a highly knowledgeable expert. Here is a long context for caching: "
    + ("The history of computing spans decades. " * 50)  # ~350 tokens
)


def _run_c4(owner_jwt: str, gemini_key: str) -> None:
    k = _api_key(owner_jwt, "c4")
    large_msg = [
        {"role": "user", "content": _LARGE_CONTEXT_PREFIX + f" What is 2+2? run_id={run_id}"},
    ]
    time.sleep(EDGE_PACE_S)
    s1, b1, _ = _post_json(
        "/v1/chat/completions",
        {"model": HELIOS_GEMINI_MODEL, "messages": large_msg, "max_tokens": 16},
        {"Authorization": f"Bearer {k['key']}"},
        gemini_key=gemini_key,
    )
    time.sleep(EDGE_PACE_S)
    s2, b2, _ = _post_json(
        "/v1/chat/completions",
        {"model": HELIOS_GEMINI_MODEL, "messages": large_msg, "max_tokens": 16},
        {"Authorization": f"Bearer {k['key']}"},
        gemini_key=gemini_key,
    )

    ok_both_200 = s1 == 200 and s2 == 200
    record("C4a both large-context requests return 200", "PASS" if ok_both_200 else "FAIL",
           f"s1={s1} s2={s2}")

    # Check 2nd response for cache detail
    cache_detail_present = False
    if isinstance(b2, dict):
        usage2 = b2.get("usage", {})
        pt_details = usage2.get("prompt_tokens_details", {})
        # Gemini may surface cached_tokens or cache_read_input_tokens in pt_details
        cache_detail_present = bool(
            pt_details.get("cached_tokens") is not None
            or usage2.get("cache_read_input_tokens") is not None
            or usage2.get("cache_creation_input_tokens") is not None
        )
    record("C4b 2nd response surfaces cached/cache-creation token detail in usage",
           "PASS" if cache_detail_present else "FAIL",
           f"usage2={b2.get('usage', {}) if isinstance(b2, dict) else None!r}")

    # Both rows must have cost>0
    rows = _poll_usage_row(k["key_id"], min_rows=2)
    if len(rows) >= 2:
        ok_cost = rows[0]["cost_usd"] > 0 and rows[1]["cost_usd"] > 0
        record("C4c both usage rows billed cost_usd>0", "PASS" if ok_cost else "FAIL",
               f"cost1={rows[0]['cost_usd']} cost2={rows[1]['cost_usd']}")
    else:
        record("C4c both usage rows billed cost_usd>0", "FAIL",
               f"only {len(rows)} rows found (expected 2)")


# ---------------------------------------------------------------------------
# C5 — Disconnect billing: partial floor stamped (provider_cost, cost_usd=0)
# ---------------------------------------------------------------------------


def _run_c5(owner_jwt: str, gemini_key: str) -> None:
    k = _api_key(owner_jwt, "c5")
    payload: dict[str, Any] = {
        "model": HELIOS_GEMINI_MODEL,
        "messages": [
            {"role": "user",
             "content": f"Write a very long essay (1000 words) about Python. run_id={run_id}"},
        ],
        "max_tokens": 512,
        "stream": True,
    }
    url = f"{BASE}/v1/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {k['key']}",
        },
        method="POST",
    )

    abort_after_bytes = 512  # read a partial payload then abort
    try:
        resp = urllib.request.urlopen(req, timeout=30, context=_tls_ctx())  # noqa: S310
        _read = resp.read(abort_after_bytes)
        # Forcibly close WITHOUT reading to end — triggers server-side disconnect
        resp.close()
        started = True
    except urllib.error.HTTPError as exc:
        exc.read()
        started = exc.code == 200
    except Exception as exc:
        started = False
        _print(f"  C5 connect error: {_redact(str(exc), gemini_key)}")

    record("C5a stream started (200) then aborted mid-flight",
           "PASS" if started else "FAIL",
           f"started={started}")

    # Poll for exactly 1 partial-floor row: provider_cost present, cost_usd=0
    rows = _poll_usage_row(k["key_id"], ceiling_s=45)
    if rows:
        row = rows[0]
        ok_partial = (
            row["cost_usd"] == 0.0
            and row["provider_cost"] is not None
            and row["usage_source"] in ("partial_floor", "stream_disconnect", "frame", "stream_fallback")
        )
        record("C5b disconnect row: cost_usd=0 AND provider_cost present (partial floor)",
               "PASS" if ok_partial else "FAIL",
               f"cost_usd={row['cost_usd']} provider_cost={row['provider_cost']} "
               f"usage_source={row['usage_source']!r}")
        ok_single = len(rows) == 1
        record("C5c exactly 1 usage row (no duplicate)", "PASS" if ok_single else "FAIL",
               f"rows={len(rows)}")
    else:
        record("C5b disconnect row: cost_usd=0 AND provider_cost present (partial floor)",
               "FAIL", "no usage row appeared within polling window")
        record("C5c exactly 1 usage row (no duplicate)", "FAIL", "no usage row")


# ---------------------------------------------------------------------------
# C6 — Back-pressure: ≥1 503 ERR_OVERLOADED under burst; then 200 succeeds
# ---------------------------------------------------------------------------


def _run_c6(owner_jwt: str, gemini_key: str, cap: int) -> None:
    k = _api_key(owner_jwt, "c6")
    burst = cap + 2  # enough to saturate the cap

    statuses: list[int] = []
    lock = threading.Lock()

    def _fire() -> None:
        s, _, _ = _post_json(
            "/v1/chat/completions",
            {
                "model": HELIOS_GEMINI_MODEL,
                "messages": [{"role": "user", "content": f"hi {run_id}"}],
                "max_tokens": 8,
            },
            {"Authorization": f"Bearer {k['key']}"},
            gemini_key=gemini_key,
        )
        with lock:
            statuses.append(s)

    threads = [threading.Thread(target=_fire) for _ in range(burst)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    has_503 = any(s == 503 for s in statuses)
    record("C6a concurrent burst ≥1 503 ERR_OVERLOADED",
           "PASS" if has_503 else "FAIL",
           f"statuses={statuses}")

    # After burst settles, a single request must succeed
    time.sleep(2)
    s_single, b_single, _ = _post_json(
        "/v1/chat/completions",
        {
            "model": HELIOS_GEMINI_MODEL,
            "messages": [{"role": "user", "content": f"single post-burst {run_id}"}],
            "max_tokens": 8,
        },
        {"Authorization": f"Bearer {k['key']}"},
        gemini_key=gemini_key,
    )
    ok_single = s_single == 200
    record("C6b subsequent single request returns 200 (capacity recovered)",
           "PASS" if ok_single else "FAIL",
           f"status={s_single}")


# ---------------------------------------------------------------------------
# C7 — Governance: no-Bearer → 401; 0 usage rows
# ---------------------------------------------------------------------------


def _run_c7() -> None:
    # Snapshot the GLOBAL usage_records count before the rejected call so we can
    # prove the contract's "0 rows" — a no-Bearer call must write NO row anywhere.
    before = int(psql("SELECT count(*) FROM usage_records") or "0")

    s, b, _ = _post_json(
        "/v1/chat/completions",
        {
            "model": HELIOS_GEMINI_MODEL,
            "messages": [{"role": "user", "content": f"no-auth test {run_id}"}],
            "max_tokens": 8,
        },
        {},  # empty headers — no Authorization
    )
    ok_401 = s == 401
    record("C7a no-Bearer chat → 401", "PASS" if ok_401 else "FAIL",
           f"status={s} body={b}")

    # Governance rejects BEFORE billing — assert the global row count did not grow.
    time.sleep(3)
    after = int(psql("SELECT count(*) FROM usage_records") or "0")
    ok_zero_rows = after == before
    record("C7b no-Bearer call writes 0 usage rows (count unchanged)",
           "PASS" if ok_zero_rows else "FAIL",
           f"rows before={before} after={after} (delta={after - before})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    # ── Resolve key FIRST — exit 2 if absent ─────────────────────────────────
    gemini_key = resolve_gemini_key()

    _print(f"\nHelios live-smoke  run_id={run_id}  BASE={BASE}")
    _print("=" * 64)

    # ── Determine C6 applicability BEFORE provisioning ────────────────────────
    c6_env = dict(os.environ)
    c6_applicable = applicable("C6", c6_env)
    try:
        _cap = int(os.environ.get("GATEWAY_MAX_CONCURRENT_REQUESTS", "0").strip())
    except (ValueError, TypeError):
        _cap = 0

    if c6_applicable == "SKIP":
        _print("  [C6] GATEWAY_MAX_CONCURRENT_REQUESTS unset/0 → C6 will be SKIPPED")

    # ── Seed catalog (before restart) ─────────────────────────────────────────
    _print("\n── Seeding Gemini model + pricing ──")
    _seed_helios_model()

    # ── Restart gateway so provider_resolver picks up seeded rows ─────────────
    _print("\n── Restarting gateway ──")
    _restart_gateway_and_wait()
    time.sleep(EDGE_SETTLE_S)

    # ── Provision a single tenant for all criteria ────────────────────────────
    _print("\n── Provisioning tenant ──")
    tenant = _signup_login_key("main")
    _print(f"  tenant={tenant['email']}")

    # PUT the BYOK Gemini key — secret goes through a redacted PUT body
    owner_hdrs = {"Authorization": f"Bearer {tenant['owner_jwt']}"}
    s, b, _ = _put_json(
        f"/admin/provider-keys/{HELIOS_GEMINI_PROVIDER}",
        {"secret": gemini_key},  # key in PUT body only; never logged
        owner_hdrs,
        gemini_key=gemini_key,
    )
    byok_ok = s in (200, 204)
    record("PROVISION PUT /admin/provider-keys/google", "PASS" if byok_ok else "FAIL",
           f"status={s}", gemini_key)
    if not byok_ok:
        _print(f"HARD-STOP: BYOK PUT failed status={s}", gemini_key)
        sys.exit(1)

    # ── Run criteria ──────────────────────────────────────────────────────────
    _print("\n── C1: Coding chat (non-stream) ──")
    _run_c1(tenant["owner_jwt"], gemini_key)

    _print("\n── C2: Streaming parallel tool-calls ──")
    _run_c2(tenant["owner_jwt"], gemini_key)

    _print("\n── C3: Reasoning passthrough ──")
    _run_c3(tenant["owner_jwt"], gemini_key)

    _print("\n── C4: Prompt-cache ──")
    _run_c4(tenant["owner_jwt"], gemini_key)

    _print("\n── C5: Disconnect billing ──")
    _run_c5(tenant["owner_jwt"], gemini_key)

    _print("\n── C6: Back-pressure (conditional) ──")
    if c6_applicable == "SKIP":
        record("C6 back-pressure burst", "SKIP",
               "GATEWAY_MAX_CONCURRENT_REQUESTS unset/0 — criterion not applicable this run")
    else:
        _run_c6(tenant["owner_jwt"], gemini_key, _cap)

    _print("\n── C7: Governance (no-Bearer) ──")
    _run_c7()

    # ── Summary ───────────────────────────────────────────────────────────────
    _print("\n" + "=" * 64)
    _print("FINAL RESULTS — Helios live-smoke v34")
    _print("=" * 64)
    for criterion, status, note in RESULTS:
        _print(f"  {status}  {criterion}", gemini_key)
        if status == "FAIL":
            _print(f"       evidence: {note}", gemini_key)

    code = exit_code(RESULTS)
    passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
    skipped = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    failed = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    total = len(RESULTS)
    _print(
        f"\nHelios smoke: {passed} PASS / {skipped} SKIP / {failed} FAIL  "
        f"(total={total}, run_id={run_id})",
        gemini_key,
    )

    if code == 0:
        _print("ALL APPLICABLE CRITERIA PASS")
    else:
        _print("FAILURES DETECTED — see table above")

    # Explicit: check no secret leaked into any printed result
    for _crit, _status, note in RESULTS:
        if gemini_key and gemini_key in note:
            _print("HARD-STOP: gemini_key found in results output — secret leak!", gemini_key)
            sys.exit(1)

    sys.exit(code)


if __name__ == "__main__":
    main()
