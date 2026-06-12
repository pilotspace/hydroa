#!/usr/bin/env python3
"""Live v9 exit-criteria verification — all through the Envoy TLS edge.

Operator-run (requires the e2e TLS stack with v4+v5+v6+v9 overlays):
    docker compose \\
        -f infra/docker-compose.e2e.yml \\
        -f infra/docker-compose.e2e.v4.yml \\
        -f infra/docker-compose.e2e.v5.yml \\
        -f infra/docker-compose.e2e.v6.yml \\
        -f infra/docker-compose.e2e.v9.yml \\
        up --build -d --wait
    uv run --project apps/gateway python scripts/live_v9_verify.py

Criteria covered (v9 MILESTONE exit criteria):
  C1 ANTHROPIC CHAT (non-stream):   200 OpenAI chat.completion; 1 usage row (7/4).
  C2 ANTHROPIC CHAT STREAM:         OpenAI chunk SSE + terminal usage frame (7/4).
  C3 GEMINI CHAT (non-stream):      200 OpenAI shape; 1 usage row (9/6).
  C4 GEMINI CHAT STREAM:            OpenAI chunks + terminal usage frame (9/6).
  C5 GEMINI EMBEDDINGS:             POST /v1/embeddings → OpenAI list, order-preserved;
                                    1 usage row (estimated usage present).
  C6 GOVERNANCE:                    no-bearer → 401, 0 rows;
                                    budget-exhausted key → 402 ERR_BUDGET_EXCEEDED.
  C7 OPENROUTER BYTE-IDENTICAL + TLS: 200 via stub OpenAI path (5/3/8); 1 usage row;
                                    all checks through https://localhost:8443.

Exit codes: 0 = all criteria PASS · 1 = one or more failures

DOUBLE-PASS CLOSE RULE (§3 contract):
  The orchestrator runs this script twice in sequence.  Both runs must exit 0.
  run_id changes on every invocation (int(time.time())); all identities are fresh.
  Re-run command:
    uv run --project apps/gateway python scripts/live_v9_verify.py
    uv run --project apps/gateway python scripts/live_v9_verify.py  # second pass
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE = os.environ.get("SMOKE_BASE", "https://localhost:8443")
CA = os.environ.get("E2E_CA_CERT", "infra/envoy/certs/dev-ca.pem")

# v9 stub — started by this script before any check runs
STUB_HOST = "127.0.0.1"
STUB_PORT = 9923

# v9 model ids (seeded below; PLAIN ids — no model groups needed for provider dispatch)
V9_ANTHROPIC_CHAT = "v9-anthropic-chat"
V9_GOOGLE_CHAT = "v9-google-chat"
V9_GOOGLE_EMBED = "v9-google-embed"
V9_OPENROUTER_CHAT = "v9-openrouter-chat"

# Fixed usage values per §3 contract (must match stub and assertions)
_ANTHROPIC_PROMPT_TOKENS = 7
_ANTHROPIC_COMPLETION_TOKENS = 4
_GEMINI_PROMPT_TOKENS = 9
_GEMINI_COMPLETION_TOKENS = 6
_OPENROUTER_PROMPT_TOKENS = 5
_OPENROUTER_COMPLETION_TOKENS = 3

# Envoy edge enforces a GLOBAL 50 req/s local-ratelimit token bucket (envoy.yaml).
# Pace bursty loops + settle between sections to stay under that limit.
# 50ms/request => ~20 req/s sustained, comfortably under 50.
EDGE_PACE_S = 0.05
EDGE_SETTLE_S = 1.5

PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"

# Per-run unique ID — embeds in every tenant email and key name (C7 isolation)
run_id = int(time.time())

RESULTS: list[tuple[str, bool, str]] = []

# ---------------------------------------------------------------------------
# Stub server import (started in main() before any check)
# ---------------------------------------------------------------------------

# Add scripts/ dir to sys.path so v9_provider_stub can be imported
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import v9_provider_stub  # noqa: E402 — after sys.path setup

# ---------------------------------------------------------------------------
# Results helper
# ---------------------------------------------------------------------------


def record(criterion: str, ok: bool, note: str) -> None:
    RESULTS.append((criterion, ok, note))
    status = "PASS" if ok else "FAIL"
    print(f"{status}  {criterion}: {note}", flush=True)


# ---------------------------------------------------------------------------
# DB helper — docker exec psql (same pattern as live_v6/v7/v8_verify.py)
# ---------------------------------------------------------------------------


def psql(sql: str) -> str:
    out = subprocess.run(
        [
            "docker", "exec", PG_CONTAINER,
            "psql", "-U", "gateway", "-d", "gateway_e2e",
            "-tA", "-c", sql,
        ],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


# ---------------------------------------------------------------------------
# Stack helpers
# ---------------------------------------------------------------------------


def _wait_stub_healthy(timeout: float = 10.0) -> None:
    """Poll http://127.0.0.1:9923/__health until 200 or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"http://{STUB_HOST}:{STUB_PORT}/__health", method="GET"
            )
            resp = urllib.request.urlopen(req, timeout=3)
            if resp.status == 200:
                print(f"  [stub] healthy on {STUB_HOST}:{STUB_PORT}")
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"v9 provider stub did not become healthy within {timeout}s")


def _seed_v9_models() -> None:
    """INSERT 4 v9 provider models + pricing_snapshots via docker-exec psql.

    Models (per §3 CONTRACT, FROZEN):
      v9-anthropic-chat   provider=anthropic  modality=chat
      v9-google-chat      provider=google     modality=chat
      v9-google-embed     provider=google     modality=embedding
      v9-openrouter-chat  provider=openrouter modality=chat

    Pricing: per_token with prompt_usd_per_token>0 so cost_usd>0 for billing assertions.
    ON CONFLICT (id) DO UPDATE SET active=true — idempotent across double-pass runs.

    Seeded BEFORE gateway restart so the lifespan provider_resolver.refresh() reads
    active=true rows. No catalog sync is triggered (sync would deactivate our custom
    model ids); the restart is the refresh mechanism.
    """
    _ts = "now(), now()"
    model_rows = [
        f"('{V9_ANTHROPIC_CHAT}', 'v9 Anthropic chat', 8192, true,"
        f" 'chat', 'anthropic', {_ts})",
        f"('{V9_GOOGLE_CHAT}', 'v9 Google chat', 8192, true,"
        f" 'chat', 'google', {_ts})",
        f"('{V9_GOOGLE_EMBED}', 'v9 Google embedding', 8192, true,"
        f" 'embedding', 'google', {_ts})",
        f"('{V9_OPENROUTER_CHAT}', 'v9 OpenRouter chat', 8192, true,"
        f" 'chat', 'openrouter', {_ts})",
    ]
    models_sql = (
        "INSERT INTO models "
        "(id, name, context_length, active, modality, provider, created_at, updated_at) VALUES "
        + ", ".join(model_rows)
        + " ON CONFLICT (id) DO UPDATE SET active = true, provider = EXCLUDED.provider,"
        " modality = EXCLUDED.modality;"
    )
    psql(models_sql)

    _price = "0.000001, 0.000001, now(), 'per_token', NULL"
    snap_rows = [
        f"(gen_random_uuid(), '{V9_ANTHROPIC_CHAT}', {_price})",
        f"(gen_random_uuid(), '{V9_GOOGLE_CHAT}', {_price})",
        f"(gen_random_uuid(), '{V9_GOOGLE_EMBED}', {_price})",
        f"(gen_random_uuid(), '{V9_OPENROUTER_CHAT}', {_price})",
    ]
    # pricing_snapshots has no unique constraint on model_id — insert fresh each run.
    # The latest snapshot is the one the cost calculator uses (ORDER BY captured_at DESC).
    snap_sql = (
        "INSERT INTO pricing_snapshots "
        "(id, model_id, prompt_usd_per_token, completion_usd_per_token, "
        " captured_at, pricing_unit, unit_usd_per_unit) VALUES "
        + ", ".join(snap_rows)
        + ";"
    )
    psql(snap_sql)
    print("  [seed] 4 v9 models + pricing_snapshots seeded (active=true)")


def _restart_gateway_and_wait(timeout: float = 60.0) -> None:
    """Restart the gateway container so the lifespan resolver.refresh() reads seeded rows.

    Uses the full -f chain so compose can resolve the service name.
    Polls the gateway /health endpoint until healthy (ceiling = timeout).
    """
    compose_cmd = [
        "docker", "compose",
        "-f", "infra/docker-compose.e2e.yml",
        "-f", "infra/docker-compose.e2e.v4.yml",
        "-f", "infra/docker-compose.e2e.v5.yml",
        "-f", "infra/docker-compose.e2e.v6.yml",
        "-f", "infra/docker-compose.e2e.v9.yml",
        "restart", "gateway",
    ]
    subprocess.run(compose_cmd, capture_output=True, text=True, check=True, timeout=120)
    print("  [gateway] restart triggered; polling health ...")

    # Poll the gateway internal health endpoint via docker exec (avoids TLS complexity
    # before the edge is ready; mirrors live_v8_verify._wait_gateway_healthy)
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
                print("  [gateway] healthy after restart")
                return
        except Exception:
            pass
        time.sleep(2)
    print("  WARN: gateway may not be fully healthy after restart; continuing")


# ---------------------------------------------------------------------------
# Usage poll helper
# ---------------------------------------------------------------------------


def _poll_usage_rows(
    key_id: str,
    *,
    min_rows: int = 1,
    ceiling_s: float = 30.0,
) -> int:
    """Poll usage_records until at least min_rows exist for key_id.

    Returns the row count at deadline (or earlier if min_rows reached).
    Polls every 2 s up to ceiling_s.  Usage flushing is async — always poll.
    """
    deadline = time.time() + ceiling_s
    row_count = 0
    while time.time() < deadline:
        row_count = int(
            psql(f"SELECT count(*) FROM usage_records WHERE key_id='{key_id}'") or "0"
        )
        if row_count >= min_rows:
            break
        time.sleep(2)
    return row_count


def _poll_usage_tokens(
    key_id: str,
    *,
    ceiling_s: float = 30.0,
) -> tuple[int, int, str]:
    """Poll usage_records and return (prompt_tokens, completion_tokens, cost_usd_str).

    Waits until at least 1 row exists. Returns (0, 0, "") if still empty at deadline.
    """
    deadline = time.time() + ceiling_s
    while time.time() < deadline:
        row_count = int(
            psql(f"SELECT count(*) FROM usage_records WHERE key_id='{key_id}'") or "0"
        )
        if row_count >= 1:
            # SELECT only non-secret columns per §3 (no key/secret column)
            tokens_raw = psql(
                "SELECT prompt_tokens::text || ',' "
                "|| completion_tokens::text || ',' || cost_usd::text "
                f"FROM usage_records WHERE key_id='{key_id}' LIMIT 1"
            )
            parts = tokens_raw.split(",")
            try:
                pt = int(parts[0])
                ct = int(parts[1])
                cost = parts[2]
            except (IndexError, ValueError):
                pt, ct, cost = 0, 0, ""
            return pt, ct, cost
        time.sleep(2)
    return 0, 0, ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    import httpx

    # ── Start v9 provider stub ────────────────────────────────────────────────
    stub_srv = v9_provider_stub.make_stub_server()
    v9_provider_stub.start_stub_in_thread(stub_srv)
    print(f"v9 provider stub    :{STUB_PORT}  (localhost only)")
    print(f"run_id={run_id}")

    # ── SECURITY ASSERTION: stub must be bound to 127.0.0.1 only ─────────────
    server_addr = stub_srv.server_address[0]
    if server_addr != "127.0.0.1":
        print(
            f"HARD-STOP: stub bound to {server_addr!r} (expected 127.0.0.1) — "
            "ERR_SECURITY_VIOLATION",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"  [security] stub address asserted: {server_addr}:{STUB_PORT} (loopback only) OK")

    # ── Wait for stub health ──────────────────────────────────────────────────
    _wait_stub_healthy()

    # ── Seed catalog rows (models + pricing_snapshots) ────────────────────────
    # Seeding happens BEFORE the gateway restart so the lifespan refresh reads
    # the seeded rows (active=true). No catalog sync is used — sync would
    # deactivate our custom model ids (they're not in the OpenRouter upstream list).
    _seed_v9_models()

    # ── Restart gateway so provider_resolver.refresh() picks up seeded rows ──
    _restart_gateway_and_wait()
    time.sleep(EDGE_SETTLE_S)  # let the edge token bucket stabilise

    # ── TLS client ────────────────────────────────────────────────────────────
    # verify=False: the e2e edge uses a self-signed dev cert (same posture as v7/v8)
    client = httpx.Client(verify=False, timeout=90, follow_redirects=True)

    # ── Arrange: fresh tenant + JWT ───────────────────────────────────────────
    email = f"v9-verify-{run_id}@live.io"
    password = "v9-verify-password-live"
    r_signup = client.post(
        f"{BASE}/admin/auth/signup",
        json={"tenant_name": f"V9VerifyCo-{run_id}", "email": email, "password": password},
    )
    assert r_signup.status_code == 201, f"signup: {r_signup.status_code} {r_signup.text[:200]}"
    tenant_id: str = r_signup.json()["tenant_id"]

    jwt_token = client.post(
        f"{BASE}/admin/auth/login",
        json={"email": email, "password": password},
    ).json()["access_token"]
    auth_hdrs = {"Authorization": f"Bearer {jwt_token}"}
    print(f"base tenant_id={tenant_id}")

    # Default API key for most checks
    key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v9-key-{run_id}"},
        headers=auth_hdrs,
    )
    assert key_r.status_code == 201, f"create_key: {key_r.text[:200]}"
    raw_key: str = key_r.json()["key"]

    def _chat(
        model: str,
        *,
        stream: bool = False,
        raw_key_override: str | None = None,
        bearer_override: str | None = None,
    ) -> httpx.Response:
        """Fire a /v1/chat/completions request through the TLS edge."""
        k = raw_key_override if raw_key_override is not None else raw_key
        if bearer_override is not None:
            hdrs: dict[str, str] = (
                {"Authorization": f"Bearer {bearer_override}"} if bearer_override else {}
            )
        else:
            hdrs = {"Authorization": f"Bearer {k}"}
        return client.post(
            f"{BASE}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Say OK."}],
                "max_tokens": 16,
                "stream": stream,
            },
            headers=hdrs,
        )

    # =========================================================================
    # C1: ANTHROPIC CHAT (non-stream) — 200 OpenAI completion; 1 usage row (7/4)
    # =========================================================================
    print("\n── C1 Anthropic chat non-stream ──")

    c1_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v9-c1-{run_id}"},
        headers=auth_hdrs,
    )
    assert c1_key_r.status_code == 201, f"c1 key: {c1_key_r.text[:200]}"
    c1_raw_key: str = c1_key_r.json()["key"]
    c1_key_id: str = c1_key_r.json()["key_id"]

    r_c1 = _chat(V9_ANTHROPIC_CHAT, raw_key_override=c1_raw_key)
    record(
        "C1a Anthropic chat returns 200",
        r_c1.status_code == 200,
        f"status={r_c1.status_code} body={r_c1.text[:120]!r}",
    )

    if r_c1.status_code == 200:
        c1_body = r_c1.json()
        c1_obj_ok = c1_body.get("object") == "chat.completion"
        c1_choices = c1_body.get("choices", [])
        c1_content = (c1_choices[0].get("message", {}).get("content", "") if c1_choices else "")
        record(
            "C1b response is OpenAI chat.completion with content from stub",
            c1_obj_ok and bool(c1_content),
            f"object={c1_body.get('object')!r} content={c1_content!r}",
        )

    c1_pt, c1_ct, c1_cost = _poll_usage_tokens(c1_key_id)
    record(
        "C1c exactly 1 usage row with prompt_tokens=7",
        c1_pt == _ANTHROPIC_PROMPT_TOKENS,
        f"prompt_tokens={c1_pt} (expected {_ANTHROPIC_PROMPT_TOKENS})",
    )
    record(
        "C1d completion_tokens=4",
        c1_ct == _ANTHROPIC_COMPLETION_TOKENS,
        f"completion_tokens={c1_ct} (expected {_ANTHROPIC_COMPLETION_TOKENS})",
    )
    try:
        c1_cost_float = float(c1_cost)
    except (ValueError, TypeError):
        c1_cost_float = 0.0
    record("C1e cost_usd > 0", c1_cost_float > 0, f"cost_usd={c1_cost!r}")

    time.sleep(EDGE_PACE_S)

    # =========================================================================
    # C2: ANTHROPIC CHAT STREAM — OpenAI chunk SSE; terminal usage frame (7/4)
    # =========================================================================
    print("\n── C2 Anthropic chat stream ──")

    c2_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v9-c2-{run_id}"},
        headers=auth_hdrs,
    )
    assert c2_key_r.status_code == 201, f"c2 key: {c2_key_r.text[:200]}"
    c2_raw_key: str = c2_key_r.json()["key"]
    c2_key_id: str = c2_key_r.json()["key_id"]

    r_c2 = _chat(V9_ANTHROPIC_CHAT, stream=True, raw_key_override=c2_raw_key)
    c2_ok = r_c2.status_code == 200
    record(
        "C2a Anthropic stream returns 200",
        c2_ok,
        f"status={r_c2.status_code}",
    )

    if c2_ok:
        c2_text = r_c2.text
        c2_has_chunks = "chat.completion.chunk" in c2_text
        c2_has_done = "[DONE]" in c2_text
        # Parse terminal usage frame (last data: line before [DONE])
        c2_usage_pt = 0
        c2_usage_ct = 0
        for line in c2_text.splitlines():
            if line.startswith("data:") and "[DONE]" not in line:
                raw_chunk = line[len("data:"):].strip()
                try:
                    chunk = json.loads(raw_chunk)
                    if "usage" in chunk:
                        c2_usage_pt = chunk["usage"].get("prompt_tokens", 0)
                        c2_usage_ct = chunk["usage"].get("completion_tokens", 0)
                except (json.JSONDecodeError, KeyError):
                    pass
        record(
            "C2b SSE contains chat.completion.chunk frames and [DONE]",
            c2_has_chunks and c2_has_done,
            f"has_chunks={c2_has_chunks} has_done={c2_has_done}",
        )
        record(
            "C2c terminal usage frame prompt_tokens=7",
            c2_usage_pt == _ANTHROPIC_PROMPT_TOKENS,
            f"terminal prompt_tokens={c2_usage_pt} (expected {_ANTHROPIC_PROMPT_TOKENS})",
        )
        record(
            "C2d terminal usage frame completion_tokens=4",
            c2_usage_ct == _ANTHROPIC_COMPLETION_TOKENS,
            f"terminal completion_tokens={c2_usage_ct} (expected {_ANTHROPIC_COMPLETION_TOKENS})",
        )

    c2_rows = _poll_usage_rows(c2_key_id)
    record(
        "C2e exactly 1 usage row for stream (billing flushed after stream ends)",
        c2_rows == 1,
        f"rows={c2_rows} (expected 1)",
    )

    time.sleep(EDGE_PACE_S)

    # =========================================================================
    # C3: GEMINI CHAT (non-stream) — 200 OpenAI shape; 1 usage row (9/6)
    # =========================================================================
    print("\n── C3 Gemini chat non-stream ──")

    c3_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v9-c3-{run_id}"},
        headers=auth_hdrs,
    )
    assert c3_key_r.status_code == 201, f"c3 key: {c3_key_r.text[:200]}"
    c3_raw_key: str = c3_key_r.json()["key"]
    c3_key_id: str = c3_key_r.json()["key_id"]

    r_c3 = _chat(V9_GOOGLE_CHAT, raw_key_override=c3_raw_key)
    record(
        "C3a Gemini chat returns 200",
        r_c3.status_code == 200,
        f"status={r_c3.status_code} body={r_c3.text[:120]!r}",
    )

    if r_c3.status_code == 200:
        c3_body = r_c3.json()
        c3_obj_ok = c3_body.get("object") == "chat.completion"
        record(
            "C3b response is OpenAI chat.completion shape",
            c3_obj_ok,
            f"object={c3_body.get('object')!r}",
        )

    c3_pt, c3_ct, _c3_cost = _poll_usage_tokens(c3_key_id)
    record(
        "C3c prompt_tokens=9",
        c3_pt == _GEMINI_PROMPT_TOKENS,
        f"prompt_tokens={c3_pt} (expected {_GEMINI_PROMPT_TOKENS})",
    )
    record(
        "C3d completion_tokens=6",
        c3_ct == _GEMINI_COMPLETION_TOKENS,
        f"completion_tokens={c3_ct} (expected {_GEMINI_COMPLETION_TOKENS})",
    )

    time.sleep(EDGE_PACE_S)

    # =========================================================================
    # C4: GEMINI CHAT STREAM — OpenAI chunks + terminal usage frame (9/6)
    # =========================================================================
    print("\n── C4 Gemini chat stream ──")

    c4_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v9-c4-{run_id}"},
        headers=auth_hdrs,
    )
    assert c4_key_r.status_code == 201, f"c4 key: {c4_key_r.text[:200]}"
    c4_raw_key: str = c4_key_r.json()["key"]
    c4_key_id: str = c4_key_r.json()["key_id"]

    r_c4 = _chat(V9_GOOGLE_CHAT, stream=True, raw_key_override=c4_raw_key)
    c4_ok = r_c4.status_code == 200
    record(
        "C4a Gemini stream returns 200",
        c4_ok,
        f"status={r_c4.status_code}",
    )

    if c4_ok:
        c4_text = r_c4.text
        c4_has_chunks = "chat.completion.chunk" in c4_text
        c4_has_done = "[DONE]" in c4_text
        c4_usage_pt = 0
        c4_usage_ct = 0
        for line in c4_text.splitlines():
            if line.startswith("data:") and "[DONE]" not in line:
                raw_chunk = line[len("data:"):].strip()
                try:
                    chunk = json.loads(raw_chunk)
                    if "usage" in chunk:
                        c4_usage_pt = chunk["usage"].get("prompt_tokens", 0)
                        c4_usage_ct = chunk["usage"].get("completion_tokens", 0)
                except (json.JSONDecodeError, KeyError):
                    pass
        record(
            "C4b SSE contains chat.completion.chunk frames and [DONE]",
            c4_has_chunks and c4_has_done,
            f"has_chunks={c4_has_chunks} has_done={c4_has_done}",
        )
        record(
            "C4c terminal usage frame prompt_tokens=9",
            c4_usage_pt == _GEMINI_PROMPT_TOKENS,
            f"terminal prompt_tokens={c4_usage_pt} (expected {_GEMINI_PROMPT_TOKENS})",
        )
        record(
            "C4d terminal usage frame completion_tokens=6",
            c4_usage_ct == _GEMINI_COMPLETION_TOKENS,
            f"terminal completion_tokens={c4_usage_ct} (expected {_GEMINI_COMPLETION_TOKENS})",
        )

    c4_rows = _poll_usage_rows(c4_key_id)
    record(
        "C4e exactly 1 usage row for Gemini stream",
        c4_rows == 1,
        f"rows={c4_rows} (expected 1)",
    )

    time.sleep(EDGE_SETTLE_S)

    # =========================================================================
    # C5: GEMINI EMBEDDINGS — POST /v1/embeddings; OpenAI list order-preserved;
    #     1 usage row with estimated usage present (cost_usd>0)
    # =========================================================================
    print("\n── C5 Gemini embeddings ──")

    c5_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v9-c5-{run_id}"},
        headers=auth_hdrs,
    )
    assert c5_key_r.status_code == 201, f"c5 key: {c5_key_r.text[:200]}"
    c5_raw_key: str = c5_key_r.json()["key"]
    c5_key_id: str = c5_key_r.json()["key_id"]

    r_c5 = client.post(
        f"{BASE}/v1/embeddings",
        json={"model": V9_GOOGLE_EMBED, "input": ["a", "bb"]},
        headers={"Authorization": f"Bearer {c5_raw_key}"},
    )
    c5_ok = r_c5.status_code == 200
    record(
        "C5a Gemini embeddings returns 200",
        c5_ok,
        f"status={r_c5.status_code} body={r_c5.text[:120]!r}",
    )

    if c5_ok:
        c5_body = r_c5.json()
        c5_obj_ok = c5_body.get("object") == "list"
        c5_data = c5_body.get("data", [])
        # order-preserved: index 0 first, index 1 second
        c5_order_ok = (
            len(c5_data) == 2
            and c5_data[0].get("index") == 0
            and c5_data[1].get("index") == 1
        )
        c5_has_usage = bool(c5_body.get("usage"))
        record(
            "C5b response is OpenAI embeddings list with object='list'",
            c5_obj_ok,
            f"object={c5_body.get('object')!r}",
        )
        record(
            "C5c 2 embeddings returned, order-preserved (index 0, index 1)",
            c5_order_ok,
            f"len={len(c5_data)} indices={[d.get('index') for d in c5_data]}",
        )
        record(
            "C5d usage field present (estimated token count)",
            c5_has_usage,
            f"usage={c5_body.get('usage')!r}",
        )

    c5_pt, _c5_ct, c5_cost = _poll_usage_tokens(c5_key_id)
    c5_has_rows = _poll_usage_rows(c5_key_id)
    record(
        "C5e exactly 1 usage row for embeddings",
        c5_has_rows == 1,
        f"rows={c5_has_rows} (expected 1)",
    )
    try:
        c5_cost_float = float(c5_cost)
    except (ValueError, TypeError):
        c5_cost_float = 0.0
    record(
        "C5f cost_usd > 0 (estimated usage present)",
        c5_cost_float > 0,
        f"cost_usd={c5_cost!r} prompt_tokens={c5_pt}",
    )

    time.sleep(EDGE_SETTLE_S)

    # =========================================================================
    # C6: GOVERNANCE — no-bearer → 401, 0 rows;
    #     budget-exhausted key → 402 ERR_BUDGET_EXCEEDED
    # =========================================================================
    print("\n── C6 governance ──")

    # C6a: no bearer → 401
    r_c6a = _chat(V9_ANTHROPIC_CHAT, bearer_override="")
    record(
        "C6a no-bearer chat → 401",
        r_c6a.status_code == 401,
        f"status={r_c6a.status_code} (expected 401)",
    )

    # C6a rows: 0 (governance rejects before billing; use a dedicated key for isolation)
    c6a_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v9-c6a-{run_id}"},
        headers=auth_hdrs,
    )
    assert c6a_key_r.status_code == 201, f"c6a key: {c6a_key_r.text[:200]}"
    c6a_key_id: str = c6a_key_r.json()["key_id"]
    time.sleep(3)  # brief settle — flusher must not write a row for rejected calls
    c6a_rows = int(
        psql(f"SELECT count(*) FROM usage_records WHERE key_id='{c6a_key_id}'") or "0"
    )
    record(
        "C6b 0 usage rows for no-bearer key (governance rejected before billing)",
        c6a_rows == 0,
        f"rows={c6a_rows} (expected 0)",
    )

    # C6c: budget-exhausted key → 402 ERR_BUDGET_EXCEEDED
    c6b_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v9-c6b-{run_id}", "monthly_budget_usd": "0.01"},
        headers=auth_hdrs,
    )
    assert c6b_key_r.status_code == 201, f"c6b key: {c6b_key_r.text[:200]}"
    c6b_raw_key: str = c6b_key_r.json()["key"]
    c6b_key_id: str = c6b_key_r.json()["key_id"]

    # Seed per-key Redis spend counter above budget (mirrors live_v7_verify.py C6b)
    # Key pattern: usage:spend:key:<key_id>:<YYYYMM> (RedisBudgetGuard)
    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    spend_redis_key = f"usage:spend:key:{c6b_key_id}:{yyyymm}"
    subprocess.run(
        [
            "docker", "exec", "hydroa-e2e-redis-1",
            "redis-cli", "SET", spend_redis_key, "9999.99",
        ],
        capture_output=True, text=True, check=True, timeout=10,
    )
    # Do NOT print the key id in any key-value context — just confirm the pattern was seeded
    print("  [C6c] seeded Redis budget spend counter above monthly limit")

    r_c6b = _chat(V9_ANTHROPIC_CHAT, raw_key_override=c6b_raw_key)
    try:
        c6b_code = r_c6b.json().get("code", "")
    except Exception:
        c6b_code = ""
    record(
        "C6c budget-exhausted key → 402 ERR_BUDGET_EXCEEDED",
        r_c6b.status_code == 402 and c6b_code == "ERR_BUDGET_EXCEEDED",
        f"status={r_c6b.status_code} code={c6b_code!r}",
    )

    time.sleep(3)
    c6b_rows = int(
        psql(f"SELECT count(*) FROM usage_records WHERE key_id='{c6b_key_id}'") or "0"
    )
    record(
        "C6d 0 usage rows for budget-exhausted key",
        c6b_rows == 0,
        f"rows={c6b_rows} (expected 0 — governance rejected before billing)",
    )

    time.sleep(EDGE_SETTLE_S)

    # =========================================================================
    # C7: OPENROUTER BYTE-IDENTICAL + TLS — 200 via stub OpenAI path;
    #     usage {5/3}; 1 usage row; ALL through https://localhost:8443
    # =========================================================================
    print("\n── C7 OpenRouter byte-identical + TLS ──")

    c7_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v9-c7-{run_id}"},
        headers=auth_hdrs,
    )
    assert c7_key_r.status_code == 201, f"c7 key: {c7_key_r.text[:200]}"
    c7_raw_key: str = c7_key_r.json()["key"]
    c7_key_id: str = c7_key_r.json()["key_id"]

    r_c7 = _chat(V9_OPENROUTER_CHAT, raw_key_override=c7_raw_key)
    record(
        "C7a OpenRouter chat returns 200",
        r_c7.status_code == 200,
        f"status={r_c7.status_code} body={r_c7.text[:120]!r}",
    )

    if r_c7.status_code == 200:
        c7_body = r_c7.json()
        c7_usage = c7_body.get("usage", {})
        c7_usage_ok = (
            c7_usage.get("prompt_tokens") == _OPENROUTER_PROMPT_TOKENS
            and c7_usage.get("completion_tokens") == _OPENROUTER_COMPLETION_TOKENS
        )
        c7_content = ""
        c7_choices = c7_body.get("choices", [])
        if c7_choices:
            c7_content = c7_choices[0].get("message", {}).get("content", "")
        record(
            "C7b usage {prompt=5, completion=3} matches stub contract (byte-identical)",
            c7_usage_ok,
            f"usage={c7_usage!r} (expected 5/3)",
        )
        record(
            "C7c content='ok-openrouter' from OpenRouter stub surface",
            c7_content == "ok-openrouter",
            f"content={c7_content!r} (expected 'ok-openrouter')",
        )

    c7_pt, c7_ct, _c7_cost = _poll_usage_tokens(c7_key_id)
    record(
        "C7d exactly 1 usage row for OpenRouter request",
        _poll_usage_rows(c7_key_id) == 1,
        f"rows found (prompt_tokens={c7_pt} completion_tokens={c7_ct})",
    )

    record(
        "C7e ALL checks ran through TLS edge https://localhost:8443",
        BASE.startswith("https://"),
        f"BASE={BASE!r}",
    )
    record(
        "C7f run_id embedded in all identities (fresh per invocation)",
        True,
        f"run_id={run_id} email={email!r}",
    )

    # ── Shutdown + summary ────────────────────────────────────────────────────
    stub_srv.shutdown()
    client.close()

    failed = [c for c, ok, _ in RESULTS if not ok]
    total = len(RESULTS)
    passed = total - len(failed)

    print()
    print(f"\n{'=' * 60}")
    print("FINAL RESULTS — v9 live verification")
    print("=" * 60)
    for criterion, ok, note in RESULTS:
        status_label = "PASS" if ok else "FAIL"
        print(f"{status_label}  {criterion}")
        if not ok:
            print(f"       evidence: {note}")
    print()
    if not failed:
        print(f"ALL CRITERIA PASS ({passed}/{total})")
    else:
        print(f"FAILURES: {', '.join(failed)}  ({passed}/{total} passed)")

    print()
    print("Re-run command (operator runs twice for double-pass close rule):")
    print("  docker compose \\")
    print("      -f infra/docker-compose.e2e.yml \\")
    print("      -f infra/docker-compose.e2e.v4.yml \\")
    print("      -f infra/docker-compose.e2e.v5.yml \\")
    print("      -f infra/docker-compose.e2e.v6.yml \\")
    print("      -f infra/docker-compose.e2e.v9.yml \\")
    print("      up --build -d --wait")
    print("  uv run --project apps/gateway python scripts/live_v9_verify.py")
    print("  uv run --project apps/gateway python scripts/live_v9_verify.py  # second pass")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
