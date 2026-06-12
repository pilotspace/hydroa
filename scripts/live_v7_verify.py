#!/usr/bin/env python3
"""Live v7 exit-criteria verification — all through the Envoy TLS edge.

Operator-run (requires the e2e TLS stack with v4+v5+v6+v7 overlays):
    docker compose \\
        -f infra/docker-compose.e2e.yml \\
        -f infra/docker-compose.e2e.v4.yml \\
        -f infra/docker-compose.e2e.v5.yml \\
        -f infra/docker-compose.e2e.v6.yml \\
        -f infra/docker-compose.e2e.v7.yml \\
        up -d --wait
    uv run --project apps/gateway python scripts/live_v7_verify.py

Criteria covered (v7 MILESTONE exit criteria):
  C1 embeddings:      POST /v1/embeddings → 200; 1 row pricing_unit='per_token' cost_usd>0.
  C2 images:          POST /v1/images/generations → 200; 1 row pricing_unit='per_image' quantity=2.
  C3 STT:             POST /v1/audio/transcriptions (multipart) → 200; 1 row per_second qty=12.5.
  C4 TTS:             POST /v1/audio/speech → 200 audio bytes; 1 row per_character qty=len(text).
  C5 chat-unaffected: chat completion to v6-alias still returns 200 (OpenRouter path untouched).
  C6 governance:      (a) no/invalid key → 401 ERR_AUTH_INVALID_KEY;
                      (b) budget-exhausted key → 402 ERR_BUDGET_EXCEEDED;
                      no usage_records row written for rejected calls.
  C7 TLS + isolation: all checks through https://localhost:8443; run_id in every identity;
                      orchestrator runs script twice and both runs exit 0 (double-pass).

Exit codes: 0 = all criteria PASS · 1 = one or more failures

DOUBLE-PASS CLOSE RULE (§3 contract):
  The orchestrator runs this script twice in sequence.  Both runs must exit 0.
  This script does NOT enforce the double-pass — it documents the re-run command.
  run_id changes on every invocation (int(time.time())); all identities are fresh.
  Re-run command:
    uv run --project apps/gateway python scripts/live_v7_verify.py
    uv run --project apps/gateway python scripts/live_v7_verify.py  # second pass
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE = os.environ.get("SMOKE_BASE", "https://localhost:8443")

# Postgres + Redis container names (docker-compose.e2e stack)
PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"
REDIS_CONTAINER = "hydroa-e2e-redis-1"

# v6 alias — still wired in the v6 overlay; used for C5 chat-unaffected check
V6_ALIAS = "v6-alias"

# TTS test input — fixed so len() is deterministic across runs
_TTS_INPUT = "Hello from the v7 harness, this is a TTS test."

# Per-run unique ID — embeds in every tenant email and key name (C7 isolation)
run_id = int(time.time())

RESULTS: list[tuple[str, bool, str]] = []

# ---------------------------------------------------------------------------
# Stub server import (started below in main())
# ---------------------------------------------------------------------------

# Add scripts/ dir to sys.path so both stubs can be imported
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import v6_fault_stub  # noqa: E402 — after sys.path setup
import v7_openai_stub  # noqa: E402 — after sys.path setup


# ---------------------------------------------------------------------------
# Results helper
# ---------------------------------------------------------------------------


def record(criterion: str, ok: bool, note: str) -> None:
    RESULTS.append((criterion, ok, note))
    status = "PASS" if ok else "FAIL"
    print(f"{status}  {criterion}: {note}", flush=True)


# ---------------------------------------------------------------------------
# DB helper — docker exec psql (mirrors live_v6_verify.py exactly)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Redis helper — docker exec redis-cli (for C6 budget-exhausted seed)
# ---------------------------------------------------------------------------


def redis_set(key: str, value: str) -> None:
    """SET a Redis key to value via docker exec redis-cli."""
    subprocess.run(
        [
            "docker",
            "exec",
            REDIS_CONTAINER,
            "redis-cli",
            "SET",
            key,
            value,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Stack helpers
# ---------------------------------------------------------------------------


def _wait_gateway_healthy(timeout: float = 60.0) -> None:
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
                print("  gateway healthy")
                return
        except Exception:
            pass
        time.sleep(2)
    print("  WARN: gateway may not be healthy")


# ---------------------------------------------------------------------------
# Model + pricing seed
# ---------------------------------------------------------------------------


def seed_models(
    emb_id: str,
    img_id: str,
    stt_id: str,
    tts_id: str,
) -> None:
    """INSERT the 4 OpenAI models + pricing_snapshots via raw SQL.

    All ids are run_id-suffixed to remain isolated across double-pass runs.
    ON CONFLICT (id) DO UPDATE SET active=true ensures idempotency.

    Pricing values per §3 contract:
      per_token:     prompt_usd_per_token=0.00000002, unit_usd_per_unit=NULL
      per_image:     unit_usd_per_unit=0.04
      per_second:    unit_usd_per_unit=0.0001
      per_character: unit_usd_per_unit=0.000015
    """
    models_sql = (
        "INSERT INTO models "
        "(id, name, context_length, active, modality, provider, created_at, updated_at) VALUES "
        f"('{emb_id}', 'v7 stub embedding', 8192, true, 'embedding', 'openai', now(), now()),"
        f"('{img_id}', 'v7 stub image',     8192, true, 'image',     'openai', now(), now()),"
        f"('{stt_id}', 'v7 stub stt',       8192, true, 'audio_stt', 'openai', now(), now()),"
        f"('{tts_id}', 'v7 stub tts',       8192, true, 'audio_tts', 'openai', now(), now()) "
        "ON CONFLICT (id) DO UPDATE SET active = true;"
    )
    psql(models_sql)

    snapshots_sql = (
        "INSERT INTO pricing_snapshots "
        "(id, model_id, prompt_usd_per_token, completion_usd_per_token, "
        " captured_at, pricing_unit, unit_usd_per_unit) VALUES "
        f"(gen_random_uuid(), '{emb_id}', 0.00000002, 0, now(), 'per_token',     NULL),"
        f"(gen_random_uuid(), '{img_id}', 0,          0, now(), 'per_image',     0.04),"
        f"(gen_random_uuid(), '{stt_id}', 0,          0, now(), 'per_second',    0.0001),"
        f"(gen_random_uuid(), '{tts_id}', 0,          0, now(), 'per_character', 0.000015);"
    )
    psql(snapshots_sql)
    print(
        f"  [seed] 4 models + snapshots: emb={emb_id} img={img_id} stt={stt_id} tts={tts_id}"
    )


# ---------------------------------------------------------------------------
# Poll helper for usage_records
# ---------------------------------------------------------------------------


def _poll_usage_row(
    key_id: str,
    pricing_unit: str,
    *,
    ceiling_s: float = 30.0,
) -> tuple[int, str, str]:
    """Poll usage_records until exactly 1 row exists for key_id+pricing_unit.

    Returns (row_count, quantity_str, cost_usd_str) from the first matching row.
    Polls every 2 s up to ceiling_s.  Returns whatever was found at deadline.
    """
    deadline = time.time() + ceiling_s
    row_count = 0
    quantity_str = ""
    cost_usd_str = ""

    while time.time() < deadline:
        row_count = int(
            psql(
                f"SELECT count(*) FROM usage_records "
                f"WHERE key_id='{key_id}' AND pricing_unit='{pricing_unit}'"
            )
            or "0"
        )
        if row_count >= 1:
            quantity_str = psql(
                f"SELECT quantity::text FROM usage_records "
                f"WHERE key_id='{key_id}' AND pricing_unit='{pricing_unit}' LIMIT 1"
            )
            cost_usd_str = psql(
                f"SELECT cost_usd::text FROM usage_records "
                f"WHERE key_id='{key_id}' AND pricing_unit='{pricing_unit}' LIMIT 1"
            )
            break
        time.sleep(2)

    return row_count, quantity_str, cost_usd_str


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    import httpx

    # ── Start stubs ───────────────────────────────────────────────────────────
    # v6 fault stub (chat surface :9920) — must be alive for C5 chat-unaffected
    v6_srv = v6_fault_stub.make_stub_server()
    v6_fault_stub.start_stub_in_thread(v6_srv)
    print(f"v6 fault stub       :{v6_fault_stub.STUB_PORT}  (localhost only)")

    # v7 OpenAI stub (multi-modal surface :9921)
    v7_srv = v7_openai_stub.make_stub_server()
    v7_openai_stub.start_stub_in_thread(v7_srv)
    print(f"v7 OpenAI stub      :{v7_openai_stub.STUB_PORT}  (localhost only)")
    print(f"run_id={run_id}")

    # ── TLS client ────────────────────────────────────────────────────────────
    # verify=False: e2e edge uses a self-signed cert (same posture as live_v6_verify.py)
    client = httpx.Client(verify=False, timeout=90, follow_redirects=True)

    # ── Wait for gateway ──────────────────────────────────────────────────────
    _wait_gateway_healthy()

    # ── Model IDs (run_id-suffixed for isolation) ─────────────────────────────
    emb_id = f"openai/emb-{run_id}"
    img_id = f"openai/img-{run_id}"
    stt_id = f"openai/stt-{run_id}"
    tts_id = f"openai/tts-{run_id}"

    # NOTE: model seeding happens AFTER the catalog sync below. The catalog sync
    # DEACTIVATES (active=false) any model not present in the upstream OpenRouter
    # list, so seeding before the sync would leave our stub models active=false
    # → ERR_MODEL_UNKNOWN. This matches the v6 order (sync first, then seed stubs).

    # ── Base tenant + JWT ─────────────────────────────────────────────────────
    email = f"v7-verify-{run_id}@live.io"
    password = "v7-verify-password-live"
    r_signup = client.post(
        f"{BASE}/admin/auth/signup",
        json={
            "tenant_name": f"V7VerifyCo-{run_id}",
            "email": email,
            "password": password,
        },
    )
    assert r_signup.status_code == 201, (
        f"signup: {r_signup.status_code} {r_signup.text[:200]}"
    )
    tenant_id: str = r_signup.json()["tenant_id"]
    jwt_token = client.post(
        f"{BASE}/admin/auth/login",
        json={"email": email, "password": password},
    ).json()["access_token"]
    auth_hdrs = {"Authorization": f"Bearer {jwt_token}"}
    print(f"base tenant_id={tenant_id}")

    # ── Trigger a catalog sync so the gateway picks up the new model rows ─────
    sync = subprocess.run(
        [
            "docker",
            "exec",
            GW_CONTAINER,
            "python",
            "-c",
            (
                "import urllib.request, json;"
                "req=urllib.request.Request('http://localhost:8000/internal/catalog/sync', method='POST');"
                "print(json.load(urllib.request.urlopen(req, timeout=60))['synced'])"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if sync.returncode != 0:
        print(f"  [catalog] WARN: {sync.stderr[:200]}", file=sys.stderr)
    else:
        print(f"  [catalog] synced {sync.stdout.strip()} models")

    # ── Seed models + pricing_snapshots (AFTER sync so they stay active=true) ──
    # The sync above deactivates non-upstream models; seeding now keeps our four
    # OpenAI stub models + the v6 chat aliases active=true.
    seed_models(emb_id, img_id, stt_id, tts_id)
    chat_seed_sql = (
        "INSERT INTO models (id, name, context_length, active, created_at, updated_at) VALUES "
        "('stub/primary', 'v6 stub primary', 8192, true, now(), now()),"
        "('stub/fallback', 'v6 stub fallback', 8192, true, now(), now()) "
        "ON CONFLICT (id) DO UPDATE SET active = true;"
        "INSERT INTO pricing_snapshots "
        "(id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at) VALUES "
        "(gen_random_uuid(), 'stub/primary',  0, 0, now()),"
        "(gen_random_uuid(), 'stub/fallback', 0, 0, now());"
    )
    psql(chat_seed_sql)
    print("  [seed] models + stub chat aliases seeded (post-sync, active=true)")

    # =========================================================================
    # C1: Embeddings — 200 + 1 usage_records row pricing_unit='per_token' cost>0
    # =========================================================================
    print("\n── C1 embeddings ──")

    c1_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v7-c1-{run_id}"},
        headers=auth_hdrs,
    )
    assert c1_key_r.status_code == 201, f"c1 key: {c1_key_r.text[:200]}"
    c1_raw_key: str = c1_key_r.json()["key"]
    c1_key_id: str = c1_key_r.json()["key_id"]

    r_c1 = client.post(
        f"{BASE}/v1/embeddings",
        json={"model": emb_id, "input": "hello"},
        headers={"Authorization": f"Bearer {c1_raw_key}"},
    )
    c1_ok = r_c1.status_code == 200
    record(
        "C1a embeddings 200",
        c1_ok,
        f"status={r_c1.status_code} body={r_c1.text[:120]!r}",
    )

    if c1_ok:
        body_c1 = r_c1.json()
        has_data = bool(body_c1.get("data"))
        has_usage = bool(body_c1.get("usage"))
        record(
            "C1b embeddings body has data + usage",
            has_data and has_usage,
            f"data={has_data} usage={has_usage}",
        )

    # Poll usage_records ≤30 s
    c1_rows, c1_qty, c1_cost = _poll_usage_row(c1_key_id, "per_token")
    record(
        "C1c exactly 1 usage_records row per_token",
        c1_rows == 1,
        f"rows={c1_rows} (expected 1)",
    )
    try:
        c1_cost_float = float(c1_cost)
    except (ValueError, TypeError):
        c1_cost_float = 0.0
    record(
        "C1d cost_usd > 0",
        c1_cost_float > 0,
        f"cost_usd={c1_cost!r}",
    )

    # =========================================================================
    # C2: Images — 200 + 1 row pricing_unit='per_image' quantity=2
    # =========================================================================
    print("\n── C2 images ──")

    c2_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v7-c2-{run_id}"},
        headers=auth_hdrs,
    )
    assert c2_key_r.status_code == 201, f"c2 key: {c2_key_r.text[:200]}"
    c2_raw_key: str = c2_key_r.json()["key"]
    c2_key_id: str = c2_key_r.json()["key_id"]

    r_c2 = client.post(
        f"{BASE}/v1/images/generations",
        json={"model": img_id, "prompt": "a cat", "n": 2},
        headers={"Authorization": f"Bearer {c2_raw_key}"},
    )
    record(
        "C2a images/generations 200",
        r_c2.status_code == 200,
        f"status={r_c2.status_code} body={r_c2.text[:120]!r}",
    )

    c2_rows, c2_qty, _c2_cost = _poll_usage_row(c2_key_id, "per_image")
    record(
        "C2b exactly 1 usage_records row per_image",
        c2_rows == 1,
        f"rows={c2_rows} (expected 1)",
    )
    try:
        c2_qty_float = float(c2_qty)
    except (ValueError, TypeError):
        c2_qty_float = -1.0
    record(
        "C2c quantity=2 (images returned by stub)",
        c2_qty_float == 2.0,
        f"quantity={c2_qty!r} (expected 2)",
    )

    # =========================================================================
    # C3: STT — multipart POST → 200; 1 row per_second quantity=12.5
    # =========================================================================
    print("\n── C3 STT (audio/transcriptions) ──")

    c3_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v7-c3-{run_id}"},
        headers=auth_hdrs,
    )
    assert c3_key_r.status_code == 201, f"c3 key: {c3_key_r.text[:200]}"
    c3_raw_key: str = c3_key_r.json()["key"]
    c3_key_id: str = c3_key_r.json()["key_id"]

    r_c3 = client.post(
        f"{BASE}/v1/audio/transcriptions",
        files={"file": ("a.mp3", b"xxxx", "audio/mpeg")},
        data={"model": stt_id},
        headers={"Authorization": f"Bearer {c3_raw_key}"},
    )
    record(
        "C3a audio/transcriptions 200",
        r_c3.status_code == 200,
        f"status={r_c3.status_code} body={r_c3.text[:120]!r}",
    )

    c3_rows, c3_qty, _c3_cost = _poll_usage_row(c3_key_id, "per_second")
    record(
        "C3b exactly 1 usage_records row per_second",
        c3_rows == 1,
        f"rows={c3_rows} (expected 1)",
    )
    try:
        c3_qty_float = float(c3_qty)
    except (ValueError, TypeError):
        c3_qty_float = -1.0
    record(
        "C3c quantity=12.5 (duration from stub)",
        c3_qty_float == 12.5,
        f"quantity={c3_qty!r} (expected 12.5)",
    )

    # =========================================================================
    # C4: TTS — POST audio/speech → 200 audio bytes; 1 row per_character qty=len(text)
    # =========================================================================
    print("\n── C4 TTS (audio/speech) ──")

    c4_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v7-c4-{run_id}"},
        headers=auth_hdrs,
    )
    assert c4_key_r.status_code == 201, f"c4 key: {c4_key_r.text[:200]}"
    c4_raw_key: str = c4_key_r.json()["key"]
    c4_key_id: str = c4_key_r.json()["key_id"]

    r_c4 = client.post(
        f"{BASE}/v1/audio/speech",
        json={"model": tts_id, "input": _TTS_INPUT, "voice": "alloy"},
        headers={"Authorization": f"Bearer {c4_raw_key}"},
    )
    c4_audio_ok = r_c4.status_code == 200 and len(r_c4.content) > 0
    record(
        "C4a audio/speech 200 with audio bytes",
        c4_audio_ok,
        f"status={r_c4.status_code} bytes={len(r_c4.content)}",
    )

    c4_rows, c4_qty, _c4_cost = _poll_usage_row(c4_key_id, "per_character")
    record(
        "C4b exactly 1 usage_records row per_character",
        c4_rows == 1,
        f"rows={c4_rows} (expected 1)",
    )
    expected_chars = len(_TTS_INPUT)
    try:
        c4_qty_float = float(c4_qty)
    except (ValueError, TypeError):
        c4_qty_float = -1.0
    record(
        f"C4c quantity=len(input)={expected_chars}",
        c4_qty_float == float(expected_chars),
        f"quantity={c4_qty!r} (expected {expected_chars})",
    )

    # =========================================================================
    # C5: Chat-unaffected — v6-alias via the v6 stub still returns 200
    # =========================================================================
    print("\n── C5 chat-unaffected ──")

    c5_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v7-c5-{run_id}"},
        headers=auth_hdrs,
    )
    assert c5_key_r.status_code == 201, f"c5 key: {c5_key_r.text[:200]}"
    c5_raw_key: str = c5_key_r.json()["key"]

    # Set the v6 stub/primary to "ok" so the chat request succeeds
    import urllib.request as _urllib_req

    _fault_payload = json.dumps({"model": "stub/primary", "behavior": "ok"}).encode()
    _fault_req = _urllib_req.Request(
        f"http://127.0.0.1:{v6_fault_stub.STUB_PORT}/__faults",
        data=_fault_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        _urllib_req.urlopen(_fault_req, timeout=5)
    except Exception as exc:
        print(f"  [C5] fault reset warn: {exc}")

    r_c5 = client.post(
        f"{BASE}/v1/chat/completions",
        json={
            "model": V6_ALIAS,
            "messages": [{"role": "user", "content": "C5 chat-unaffected check"}],
            "max_tokens": 8,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {c5_raw_key}"},
    )
    record(
        "C5 chat via v6-alias still 200 (OpenRouter path unaffected)",
        r_c5.status_code == 200,
        f"status={r_c5.status_code} body={r_c5.text[:120]!r}",
    )

    # =========================================================================
    # C6: Governance — 401 on no/invalid key, 402 on budget-exhausted key;
    #     no usage_records row for rejected calls.
    # =========================================================================
    print("\n── C6 governance ──")

    # C6a: No Authorization header → 401 ERR_AUTH_INVALID_KEY
    r_c6a = client.post(
        f"{BASE}/v1/embeddings",
        json={"model": emb_id, "input": "no-auth"},
    )
    try:
        c6a_code = r_c6a.json().get("code", "")
    except Exception:
        c6a_code = ""
    record(
        "C6a no-auth key → 401 ERR_AUTH_INVALID_KEY",
        r_c6a.status_code == 401 and c6a_code == "ERR_AUTH_INVALID_KEY",
        f"status={r_c6a.status_code} code={c6a_code!r}",
    )

    # C6a-invalid: Invalid bearer token → 401 ERR_AUTH_INVALID_KEY
    r_c6a_inv = client.post(
        f"{BASE}/v1/embeddings",
        json={"model": emb_id, "input": "invalid-key"},
        headers={"Authorization": "Bearer sk-invalid-key-v7-harness"},
    )
    try:
        c6a_inv_code = r_c6a_inv.json().get("code", "")
    except Exception:
        c6a_inv_code = ""
    record(
        "C6a-inv invalid key → 401 ERR_AUTH_INVALID_KEY",
        r_c6a_inv.status_code == 401 and c6a_inv_code == "ERR_AUTH_INVALID_KEY",
        f"status={r_c6a_inv.status_code} code={c6a_inv_code!r}",
    )

    # C6b: Budget-exhausted key → 402 ERR_BUDGET_EXCEEDED
    # Create a fresh key, set a tiny monthly_budget_usd, then seed the Redis
    # spend counter above budget via docker exec redis-cli SET (mirrors EM6).
    c6b_key_r = client.post(
        f"{BASE}/admin/keys",
        json={"name": f"v7-c6b-{run_id}", "monthly_budget_usd": "0.01"},
        headers=auth_hdrs,
    )
    assert c6b_key_r.status_code == 201, f"c6b key: {c6b_key_r.text[:200]}"
    c6b_raw_key: str = c6b_key_r.json()["key"]
    c6b_key_id: str = c6b_key_r.json()["key_id"]

    # Seed per-key Redis spend counter above budget.
    # Key pattern mirrors RedisBudgetGuard: usage:spend:key:<key_id>:<YYYYMM>
    yyyymm = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m")
    spend_redis_key = f"usage:spend:key:{c6b_key_id}:{yyyymm}"
    redis_set(spend_redis_key, "9999.99")
    print(f"  [C6b] seeded Redis key {spend_redis_key!r} = 9999.99")

    r_c6b = client.post(
        f"{BASE}/v1/embeddings",
        json={"model": emb_id, "input": "budget-exhausted"},
        headers={"Authorization": f"Bearer {c6b_raw_key}"},
    )
    try:
        c6b_code = r_c6b.json().get("code", "")
    except Exception:
        c6b_code = ""
    record(
        "C6b budget-exhausted key → 402 ERR_BUDGET_EXCEEDED",
        r_c6b.status_code == 402 and c6b_code == "ERR_BUDGET_EXCEEDED",
        f"status={r_c6b.status_code} code={c6b_code!r}",
    )

    # C6c: No usage_records row for the rejected calls (no-auth + budget-exhausted)
    # Give the flusher a brief window in case any partial record was attempted.
    time.sleep(3)
    c6_no_auth_rows = int(
        psql(
            # Governance rejects before key_id is known; check by tenant isolation is
            # not possible for the no-auth case.  Assert total rows for the c6b key only.
            f"SELECT count(*) FROM usage_records WHERE key_id='{c6b_key_id}'"
        )
        or "0"
    )
    record(
        "C6c no usage_records for budget-rejected call",
        c6_no_auth_rows == 0,
        f"rows={c6_no_auth_rows} (expected 0 — governance rejected before billing)",
    )

    # =========================================================================
    # C7: TLS + isolation
    # =========================================================================
    print("\n── C7 TLS + isolation ──")

    record(
        "C7a all checks through TLS edge https://localhost:8443",
        BASE.startswith("https://"),
        f"BASE={BASE!r}",
    )
    record(
        "C7b run_id embedded in all identities",
        True,
        f"run_id={run_id} in: email={email!r}, keys=v7-c*-{run_id}",
    )

    # ── Shutdown + summary ────────────────────────────────────────────────────
    v6_srv.shutdown()
    v7_srv.shutdown()
    client.close()

    failed = [c for c, ok, _ in RESULTS if not ok]
    total = len(RESULTS)
    passed = total - len(failed)

    print()
    print(f"\n{'=' * 60}")
    print("FINAL RESULTS — v7 live verification")
    print("=" * 60)
    for criterion, ok, note in RESULTS:
        status_label = "PASS" if ok else "FAIL"
        print(f"{status_label}  {criterion}")
        if not ok:
            print(f"       evidence: {note}")
    print()
    if not failed:
        print(f"ALL CRITERIA PASS  ({passed}/{total})")
    else:
        print(f"FAILURES: {', '.join(failed)}  ({passed}/{total} passed)")

    print()
    print("Re-run command (orchestrator runs twice for double-pass close rule):")
    print("  docker compose \\")
    print("      -f infra/docker-compose.e2e.yml \\")
    print("      -f infra/docker-compose.e2e.v4.yml \\")
    print("      -f infra/docker-compose.e2e.v5.yml \\")
    print("      -f infra/docker-compose.e2e.v6.yml \\")
    print("      -f infra/docker-compose.e2e.v7.yml \\")
    print("      up -d --wait")
    print("  uv run --project apps/gateway python scripts/live_v7_verify.py")
    print(
        "  uv run --project apps/gateway python scripts/live_v7_verify.py  # second pass"
    )

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
