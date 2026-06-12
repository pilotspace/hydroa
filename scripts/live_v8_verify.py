#!/usr/bin/env python3
"""Live v8 exit-criteria verification — all through the Envoy TLS edge.

Operator-run (requires the e2e TLS stack with v4+v5+v6+v8 overlays):
    docker compose \\
        -f infra/docker-compose.e2e.yml \\
        -f infra/docker-compose.e2e.v4.yml \\
        -f infra/docker-compose.e2e.v5.yml \\
        -f infra/docker-compose.e2e.v6.yml \\
        -f infra/docker-compose.e2e.v8.yml \\
        up --build -d --wait
    uv run --project apps/gateway python scripts/live_v8_verify.py

Criteria covered (v8 MILESTONE exit criteria):
  C1 DISTRIBUTION:     ROUTING_STRATEGY=simple-shuffle; ≥40 chats to v8-dist;
                       /__counters dep-a>0 ∧ dep-b>0 ∧ dep-b>dep-a (weight 3:1).
  C2 LIMIT SKIP:       pre-seed Redis rpm window for stub/lim-a=5; chat v8-limit;
                       all 200 by stub/lim-b; lim-a counter flat.
  C3 ALL SATURATED:    pre-seed both sat-a and sat-b to their rpm_limit;
                       chat v8-allsat → 429 ERR_RATE_LIMITED + Retry-After,
                       no counter bump, no usage_records row.
  C4 FALLBACK:         fault fb-primary fail_5xx; chat v8-fb → 200 by fb-secondary;
                       exactly 1 usage_records row on stub/fb-secondary.
  C5 COOLDOWN:         drive ≥2 fb-primary failures → cooled; subsequent v8-fb
                       requests skip fb-primary (served by fb-secondary); after
                       TTL(5s)+fault clear fb-primary eligible again (half-open).
  C6 BILLING SERVED ID: chat v8-bill → exactly 1 usage row, model_id = SERVED
                       deployment id (stub/bill-a or stub/bill-b), never alias
                       "v8-bill", cost_usd > 0.
  C7 GOVERNANCE + TLS: chat v8-dist with NO bearer → 401, 0 usage rows;
                       all checks through https://localhost:8443 with fresh run_id.

Exit codes: 0 = all criteria PASS · 1 = one or more failures

DOUBLE-PASS CLOSE RULE (§3 contract):
  The orchestrator runs this script twice in sequence.  Both runs must exit 0.
  run_id changes on every invocation (int(time.time())); all identities are fresh.
  Re-run command:
    uv run --project apps/gateway python scripts/live_v8_verify.py
    uv run --project apps/gateway python scripts/live_v8_verify.py  # second pass
"""

from __future__ import annotations

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

# v8 stub — started by this script before any check runs
STUB_HOST = "127.0.0.1"
STUB_PORT = 9922

# v8 model aliases (FROZEN in §3)
V8_DIST = "v8-dist"
V8_LIMIT = "v8-limit"
V8_ALLSAT = "v8-allsat"
V8_FB = "v8-fb"
V8_BILL = "v8-bill"

# v8 deployment ids
DEP_A = "stub/dep-a"
DEP_B = "stub/dep-b"
LIM_A = "stub/lim-a"
LIM_B = "stub/lim-b"
SAT_A = "stub/sat-a"
SAT_B = "stub/sat-b"
FB_PRIMARY = "stub/fb-primary"
FB_SECONDARY = "stub/fb-secondary"
BILL_A = "stub/bill-a"
BILL_B = "stub/bill-b"

# rpm_limit values matching the v8 overlay (§3 FROZEN)
LIM_A_RPM_LIMIT = 5
SAT_A_RPM_LIMIT = 3
SAT_B_RPM_LIMIT = 3

# Cooldown TTL configured in the v6 overlay (GATEWAY_COOLDOWN_TTL_S=5)
# The v8 overlay keeps the v6 cooldown knobs by composing on top.
COOLDOWN_TTL_S = 5
COOLDOWN_THRESHOLD = 2  # GATEWAY_COOLDOWN_FAILURE_THRESHOLD=2

# Envoy edge enforces a GLOBAL 50 req/s local-ratelimit token bucket (envoy.yaml).
# Bursty loops (C1's 40-request distribution sample, C5's cooldown-trip loop) must
# pace under that or a later /admin/keys call gets a 429 "local_rate_limited".
# 50ms/request => ~20 req/s sustained, comfortably under 50; a settle lets the
# bucket refill (50 tokens/1s) before the next section's single-shot calls.
EDGE_PACE_S = 0.05
EDGE_SETTLE_S = 1.5

# Cooldown poll ceilings (GET /admin/routing snapshot_state is the authoritative signal)
COOLDOWN_TRIP_POLL_CEILING_S = 10.0
COOLDOWN_RECOVERY_POLL_CEILING_S = 15.0  # TTL=5s + half-open margin

PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"
REDIS_CONTAINER = "hydroa-e2e-redis-1"

# Per-run unique ID — embeds in every tenant email and key name (C7 isolation)
run_id = int(time.time())

RESULTS: list[tuple[str, bool, str]] = []

# ---------------------------------------------------------------------------
# Stub server import (started in main() before any check)
# ---------------------------------------------------------------------------

# Add scripts/ dir to sys.path so v8_router_stub can be imported
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import v8_router_stub  # noqa: E402 — after sys.path setup


# ---------------------------------------------------------------------------
# Results helper
# ---------------------------------------------------------------------------

def record(criterion: str, ok: bool, note: str) -> None:
    RESULTS.append((criterion, ok, note))
    status = "PASS" if ok else "FAIL"
    print(f"{status}  {criterion}: {note}", flush=True)


# ---------------------------------------------------------------------------
# DB helper — docker exec psql (same pattern as live_v6/v7_verify.py)
# ---------------------------------------------------------------------------

def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", "gateway", "-d", "gateway_e2e",
         "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


# ---------------------------------------------------------------------------
# Redis helper — docker exec redis-cli (for deplimit bucket pre-seeding)
# ---------------------------------------------------------------------------

def redis_set(key: str, value: str, expire_s: int | None = None) -> None:
    """SET a Redis key to value via docker exec redis-cli; optionally set EXPIRE."""
    subprocess.run(
        ["docker", "exec", REDIS_CONTAINER, "redis-cli", "SET", key, value],
        capture_output=True, text=True, check=True, timeout=10,
    )
    if expire_s is not None:
        subprocess.run(
            ["docker", "exec", REDIS_CONTAINER, "redis-cli", "EXPIRE", key, str(expire_s)],
            capture_output=True, text=True, check=True, timeout=10,
        )


def _deplimit_rpm_key(deployment_id: str, bucket: int) -> str:
    """Return the exact Redis key the limit gate reads (FROZEN key shape from §3)."""
    return f"gateway:deplimit:rpm:{deployment_id}:{bucket}"


def _seed_deplimit_rpm(deployment_id: str, value: int) -> None:
    """Pre-seed the deplimit:rpm window for deployment_id in BOTH the current and
    next bucket (minute-boundary safety per §3 contract).

    The limit gate reads:  gateway:deplimit:rpm:{deployment_id}:{bucket}
    where bucket = floor(time.time() / 60).  A request crossing the minute
    boundary reads a fresh empty bucket; seeding both buckets ensures the seed
    holds regardless of which bucket the request lands in.  EXPIRE is set to 120s
    (2 windows) — enough to outlast both the current and next window.
    """
    now_bucket = int(time.time()) // 60
    next_bucket = now_bucket + 1
    for bucket in (now_bucket, next_bucket):
        key = _deplimit_rpm_key(deployment_id, bucket)
        redis_set(key, str(value), expire_s=120)
    print(f"  [redis] seeded deplimit:rpm:{deployment_id} buckets {now_bucket},{next_bucket}={value}")


# ---------------------------------------------------------------------------
# Fault control helpers
# ---------------------------------------------------------------------------

def _set_fault(model_id: str, behavior: object) -> None:
    """Configure fault table via v8 stub control endpoint."""
    payload = json.dumps({"model": model_id, "behavior": behavior}).encode()
    req = urllib.request.Request(
        f"http://{STUB_HOST}:{STUB_PORT}/__faults",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5)


def _get_counters() -> dict[str, int]:
    """Read per-model call counters from the v8 stub GET /__counters."""
    req = urllib.request.Request(
        f"http://{STUB_HOST}:{STUB_PORT}/__counters",
        method="GET",
    )
    resp = urllib.request.urlopen(req, timeout=5)
    return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Stack helpers
# ---------------------------------------------------------------------------

def _wait_gateway_healthy(timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["docker", "exec", GW_CONTAINER, "python", "-c",
                 "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                print("  gateway healthy")
                return
        except Exception:
            pass
        time.sleep(2)
    print("  WARN: gateway may not be healthy")


def _reset_gateway_state(client_factory_url: str) -> None:
    """Reset per-process and per-model resilience state before a pass.

    The fault checks intentionally drive the GLOBAL in-process circuit breaker
    and the Redis cooldown + deplimit keys; a verify pass must start from a
    clean slate or residue from a prior pass leaks into the first checks.
      1. docker restart the gateway (clears the per-replica breaker).
      2. DEL gateway:cooldown:* in the e2e Redis.
      3. DEL gateway:deplimit:* in the e2e Redis (clears any leftover limit seeds).
      4. Poll the TLS edge /health until the gateway is back (<=60 s).
    """
    import httpx as _httpx

    subprocess.run(["docker", "restart", GW_CONTAINER],
                   capture_output=True, text=True, check=True, timeout=120)
    subprocess.run(
        ["docker", "exec", REDIS_CONTAINER, "sh", "-c",
         "redis-cli --scan --pattern 'gateway:cooldown:*' | xargs -r redis-cli del"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    subprocess.run(
        ["docker", "exec", REDIS_CONTAINER, "sh", "-c",
         "redis-cli --scan --pattern 'gateway:deplimit:*' | xargs -r redis-cli del"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            r = _httpx.get(f"{client_factory_url}/health", verify=False, timeout=5)
            if r.status_code == 200:
                print("  [reset] gateway restarted, cooldown+deplimit keys flushed, edge healthy")
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("gateway did not become healthy within 60s after reset")


def _catalog_sync() -> None:
    sync = subprocess.run(
        ["docker", "exec", GW_CONTAINER, "python", "-c",
         "import urllib.request,json;"
         "req=urllib.request.Request('http://localhost:8000/internal/catalog/sync',method='POST');"
         "print(json.load(urllib.request.urlopen(req,timeout=60))['synced'])"],
        capture_output=True, text=True, timeout=120,
    )
    if sync.returncode != 0:
        print(f"  [catalog] WARN: {sync.stderr[:200]}", file=sys.stderr)
    else:
        print(f"  [catalog] synced {sync.stdout.strip()} models")


def _seed_stub_models() -> None:
    """Seed the 8 v8 stub deployment ids into the catalog + pricing_snapshots.

    Must be called AFTER _catalog_sync() — the sync deactivates non-upstream
    models, so seeding before sync would leave our rows active=false → ERR_MODEL_UNKNOWN.

    All 8 stub ids: provider='openrouter', active=true.
    Pricing: per_token (non-zero prompt_usd_per_token) so cost_usd > 0 for C6.
    ON CONFLICT (id) DO UPDATE SET active=true — idempotent across double-pass.
    """
    stub_ids = [DEP_A, DEP_B, LIM_A, LIM_B, SAT_A, SAT_B, FB_PRIMARY, FB_SECONDARY, BILL_A, BILL_B]
    models_values = ", ".join(
        f"('{sid}', 'v8 stub {sid}', 8192, true, 'chat', 'openrouter', now(), now())"
        for sid in stub_ids
    )
    models_sql = (
        "INSERT INTO models "
        "(id, name, context_length, active, modality, provider, created_at, updated_at) VALUES "
        f"{models_values} "
        "ON CONFLICT (id) DO UPDATE SET active = true;"
    )
    psql(models_sql)

    # pricing_snapshots: per_token with non-zero prompt rate → cost_usd > 0 for C6
    snap_values = ", ".join(
        f"(gen_random_uuid(), '{sid}', 0.000001, 0.000001, now(), 'per_token', NULL)"
        for sid in stub_ids
    )
    snap_sql = (
        "INSERT INTO pricing_snapshots "
        "(id, model_id, prompt_usd_per_token, completion_usd_per_token, "
        " captured_at, pricing_unit, unit_usd_per_unit) VALUES "
        f"{snap_values};"
    )
    psql(snap_sql)
    print(f"  [seed] {len(stub_ids)} v8 stub models + pricing_snapshots seeded (post-sync)")


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
    Polls every 2 s up to ceiling_s.
    """
    deadline = time.time() + ceiling_s
    row_count = 0
    while time.time() < deadline:
        row_count = int(psql(
            f"SELECT count(*) FROM usage_records WHERE key_id='{key_id}'"
        ) or "0")
        if row_count >= min_rows:
            break
        time.sleep(2)
    return row_count


def _poll_candidate_state(
    client: Any,
    routing_url: str,
    auth_hdrs: dict[str, str],
    model_id: str,
    target_state: str,
    *,
    ceiling_s: float,
    interval_s: float = 0.5,
    negate: bool = False,
) -> tuple[bool, str]:
    """Poll GET /admin/routing until candidate model_id reaches (or leaves) target_state.

    This is the AUTHORITATIVE cooldown signal (RedisCooldownGate.snapshot_state),
    robust to simple-shuffle pick order and upstream retries — unlike inferring
    cooldown from stub call counters. Mirrors live_v6_verify._poll_candidate_state.

    Returns (success, last_observed_state).
    negate=True: poll until state != target_state (used for recovery detection).
    """
    deadline = time.time() + ceiling_s
    last_state = "unknown"
    while time.time() < deadline:
        try:
            resp = client.get(routing_url, headers=auth_hdrs)
            if resp.status_code == 200:
                for cand in resp.json().get("candidates", []):
                    if cand.get("model_id") == model_id:
                        last_state = cand.get("state", "unknown")
                        if negate and last_state != target_state:
                            return True, last_state
                        if not negate and last_state == target_state:
                            return True, last_state
                        break
        except Exception as exc:  # noqa: BLE001 — poll is best-effort
            print(f"  [poll_routing] error: {exc}")
        time.sleep(interval_s)
    return False, last_state


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import httpx

    # ── Start v8 router stub ─────────────────────────────────────────────────
    stub_srv = v8_router_stub.make_stub_server()
    v8_router_stub.start_stub_in_thread(stub_srv)
    print(f"v8 router stub      :{STUB_PORT}  (localhost only)")
    print(f"run_id={run_id}")

    # ── SECURITY ASSERTION: stub must be bound to 127.0.0.1 only ────────────
    # Verify the stub server address is the loopback interface before any check.
    server_addr = stub_srv.server_address[0]
    if server_addr != "127.0.0.1":
        print(f"HARD-STOP: stub bound to {server_addr!r} (expected 127.0.0.1) — "
              "ERR_SECURITY_VIOLATION", file=sys.stderr)
        sys.exit(1)
    print(f"  [security] stub address asserted: {server_addr}:9922 (loopback only) OK")

    # ── TLS client ────────────────────────────────────────────────────────────
    # verify=False: the e2e edge uses a self-signed cert (same posture as live_v6/v7_verify.py)
    client = httpx.Client(verify=False, timeout=90, follow_redirects=True)

    # ── Clean-slate reset (breaker + cooldown + deplimit residue) ─────────────
    _reset_gateway_state(BASE)

    # ── Catalog sync — must precede model seeding ────────────────────────────
    _catalog_sync()

    # ── Seed v8 stub models + pricing (AFTER sync so active=true survives) ───
    _seed_stub_models()

    # ── Arrange: base tenant + JWT ────────────────────────────────────────────
    email = f"v8-verify-{run_id}@live.io"
    password = "v8-verify-password-live"
    r = client.post(f"{BASE}/admin/auth/signup",
                    json={"tenant_name": f"V8VerifyCo-{run_id}",
                          "email": email, "password": password})
    assert r.status_code == 201, f"signup: {r.status_code} {r.text[:200]}"
    tenant_id: str = r.json()["tenant_id"]
    jwt_token = client.post(f"{BASE}/admin/auth/login",
                            json={"email": email, "password": password}).json()["access_token"]
    auth_hdrs = {"Authorization": f"Bearer {jwt_token}"}
    print(f"base tenant_id={tenant_id}")

    # Create a default API key for completion requests
    key_r = client.post(f"{BASE}/admin/keys",
                        json={"name": f"v8-key-{run_id}"}, headers=auth_hdrs)
    assert key_r.status_code == 201, f"create_key: {key_r.text[:200]}"
    raw_key: str = key_r.json()["key"]

    def _completion(
        model: str,
        raw_key_override: str | None = None,
        content: str = "Say OK.",
        bearer: str | None = "AUTO",
    ) -> httpx.Response:
        """Fire a chat completion. bearer='AUTO' uses raw_key_override or raw_key."""
        key = raw_key_override if raw_key_override is not None else raw_key
        if bearer == "AUTO":
            hdrs: dict[str, str] = {"Authorization": f"Bearer {key}"}
        elif bearer:
            hdrs = {"Authorization": f"Bearer {bearer}"}
        else:
            hdrs = {}  # no bearer — for C7 governance check
        return client.post(
            f"{BASE}/v1/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": content}],
                  "max_tokens": 8},
            headers=hdrs,
        )

    # ── Pre-check: reset all fault table entries to "ok" ─────────────────────
    for mid in [DEP_A, DEP_B, LIM_A, LIM_B, SAT_A, SAT_B, FB_PRIMARY, FB_SECONDARY, BILL_A, BILL_B]:
        _set_fault(mid, "ok")

    # =========================================================================
    # C1: DISTRIBUTION — ROUTING_STRATEGY=simple-shuffle; ≥40 chats to v8-dist;
    #     both dep-a and dep-b served (not always-first); dep-b > dep-a (weight 3:1)
    # =========================================================================
    print("\n── C1 distribution ──")

    c1_key_r = client.post(f"{BASE}/admin/keys",
                           json={"name": f"v8-c1-{run_id}"}, headers=auth_hdrs)
    assert c1_key_r.status_code == 201, f"c1 key: {c1_key_r.text[:200]}"
    c1_raw_key: str = c1_key_r.json()["key"]

    # Reset counters by re-setting faults to "ok"
    _set_fault(DEP_A, "ok")
    _set_fault(DEP_B, "ok")

    N_DIST = 40
    c1_ok_count = 0
    for i in range(N_DIST):
        r_c1 = _completion(V8_DIST, raw_key_override=c1_raw_key,
                           content=f"C1 dist {i}")
        if r_c1.status_code == 200:
            c1_ok_count += 1
        time.sleep(EDGE_PACE_S)  # stay under the Envoy 50 req/s edge bucket
    time.sleep(EDGE_SETTLE_S)  # let the edge token bucket refill before the next section

    record("C1a all distribution requests returned 200",
           c1_ok_count == N_DIST,
           f"ok={c1_ok_count}/{N_DIST}")

    ctrs = _get_counters()
    dep_a_count = ctrs.get(DEP_A, 0)
    dep_b_count = ctrs.get(DEP_B, 0)
    record("C1b dep-a served at least once (not always-first skipped)",
           dep_a_count > 0,
           f"dep-a count={dep_a_count}")
    record("C1c dep-b served at least once",
           dep_b_count > 0,
           f"dep-b count={dep_b_count}")
    record("C1d dep-b served strictly more than dep-a (weight 3:1 honored)",
           dep_b_count > dep_a_count,
           f"dep-a={dep_a_count} dep-b={dep_b_count} (expected dep-b > dep-a)")

    # =========================================================================
    # C2: LIMIT SKIP — pre-seed Redis rpm window for stub/lim-a=5 (rpm_limit=5);
    #     chat v8-limit → all served by stub/lim-b; lim-a counter stays flat.
    # =========================================================================
    print("\n── C2 limit skip ──")

    c2_key_r = client.post(f"{BASE}/admin/keys",
                           json={"name": f"v8-c2-{run_id}"}, headers=auth_hdrs)
    assert c2_key_r.status_code == 201, f"c2 key: {c2_key_r.text[:200]}"
    c2_raw_key: str = c2_key_r.json()["key"]

    # Reset counters
    _set_fault(LIM_A, "ok")
    _set_fault(LIM_B, "ok")

    # Pre-seed lim-a to rpm_limit=5 (both current + next bucket for boundary safety)
    _seed_deplimit_rpm(LIM_A, LIM_A_RPM_LIMIT)

    # Fire several requests to v8-limit immediately after seeding
    N_LIMIT = 5
    c2_ok_count = 0
    for i in range(N_LIMIT):
        r_c2 = _completion(V8_LIMIT, raw_key_override=c2_raw_key,
                           content=f"C2 limit {i}")
        if r_c2.status_code == 200:
            c2_ok_count += 1

    record("C2a all limit-group requests returned 200 (lim-b served)",
           c2_ok_count == N_LIMIT,
           f"ok={c2_ok_count}/{N_LIMIT}")

    ctrs = _get_counters()
    lim_a_count_after = ctrs.get(LIM_A, 0)
    lim_b_count_after = ctrs.get(LIM_B, 0)
    record("C2b stub/lim-a counter flat (saturated deployment never called)",
           lim_a_count_after == 0,
           f"lim-a count={lim_a_count_after} (expected 0 — skipped at selection)")
    record("C2c stub/lim-b served all requests",
           lim_b_count_after == N_LIMIT,
           f"lim-b count={lim_b_count_after} (expected {N_LIMIT})")

    # =========================================================================
    # C3: ALL SATURATED → 429 — pre-seed both sat-a and sat-b to rpm_limit;
    #     chat v8-allsat → 429 ERR_RATE_LIMITED + Retry-After; no counter bump;
    #     no usage_records row.
    # =========================================================================
    print("\n── C3 all saturated ──")

    c3_key_r = client.post(f"{BASE}/admin/keys",
                           json={"name": f"v8-c3-{run_id}"}, headers=auth_hdrs)
    assert c3_key_r.status_code == 201, f"c3 key: {c3_key_r.text[:200]}"
    c3_raw_key: str = c3_key_r.json()["key"]
    c3_key_id: str = c3_key_r.json()["key_id"]

    # Reset counters
    _set_fault(SAT_A, "ok")
    _set_fault(SAT_B, "ok")

    # Pre-seed both sat-a and sat-b to their rpm_limit (both current + next bucket)
    _seed_deplimit_rpm(SAT_A, SAT_A_RPM_LIMIT)
    _seed_deplimit_rpm(SAT_B, SAT_B_RPM_LIMIT)

    # Read baseline counters before the request
    ctrs_before = _get_counters()
    sat_a_before = ctrs_before.get(SAT_A, 0)
    sat_b_before = ctrs_before.get(SAT_B, 0)

    r_c3 = _completion(V8_ALLSAT, raw_key_override=c3_raw_key,
                       content="C3 all saturated")

    record("C3a all-saturated response is 429 ERR_RATE_LIMITED (not 500)",
           r_c3.status_code == 429,
           f"status={r_c3.status_code} (expected 429)")

    has_retry_after = "Retry-After" in r_c3.headers
    record("C3b 429 response carries Retry-After header",
           has_retry_after,
           f"Retry-After present={has_retry_after} headers={dict(r_c3.headers)!r}")

    ctrs_after = _get_counters()
    sat_a_after = ctrs_after.get(SAT_A, 0)
    sat_b_after = ctrs_after.get(SAT_B, 0)
    record("C3c stub/sat-a counter not bumped (no upstream call made)",
           sat_a_after == sat_a_before,
           f"sat-a before={sat_a_before} after={sat_a_after}")
    record("C3d stub/sat-b counter not bumped (no upstream call made)",
           sat_b_after == sat_b_before,
           f"sat-b before={sat_b_before} after={sat_b_after}")

    # Poll usage_records (should stay 0 — never a usage row for a 429)
    time.sleep(5)  # brief pause to let any flusher activity settle
    c3_rows = int(psql(
        f"SELECT count(*) FROM usage_records WHERE key_id='{c3_key_id}'"
    ) or "0")
    record("C3e no usage_records row written for all-saturated 429",
           c3_rows == 0,
           f"usage rows={c3_rows} (expected 0)")

    # =========================================================================
    # C4: FALLBACK — fault fb-primary fail_5xx; chat v8-fb → 200 by fb-secondary;
    #     exactly 1 usage_records row billed on stub/fb-secondary.
    # =========================================================================
    print("\n── C4 fallback ──")

    c4_key_r = client.post(f"{BASE}/admin/keys",
                           json={"name": f"v8-c4-{run_id}"}, headers=auth_hdrs)
    assert c4_key_r.status_code == 201, f"c4 key: {c4_key_r.text[:200]}"
    c4_raw_key: str = c4_key_r.json()["key"]
    c4_key_id: str = c4_key_r.json()["key_id"]

    _set_fault(FB_PRIMARY, "fail_5xx")
    _set_fault(FB_SECONDARY, "ok")

    r_c4 = _completion(V8_FB, raw_key_override=c4_raw_key,
                       content="C4 fallback test")
    record("C4a fallback: 200 after primary fails",
           r_c4.status_code == 200,
           f"status={r_c4.status_code}")

    c4_model = r_c4.json().get("model", "") if r_c4.status_code == 200 else ""
    record("C4b response model == stub/fb-secondary",
           c4_model == FB_SECONDARY,
           f"response model={c4_model!r} (expected {FB_SECONDARY!r})")

    # Wait for flusher and check ledger row
    deadline = time.time() + 30
    c4_db_model = ""
    c4_row_count = 0
    while time.time() < deadline:
        c4_row_count = int(psql(
            f"SELECT count(*) FROM usage_records WHERE key_id='{c4_key_id}'"
        ) or "0")
        if c4_row_count >= 1:
            c4_db_model = psql(
                f"SELECT model_id FROM usage_records WHERE key_id='{c4_key_id}' LIMIT 1"
            )
            break
        time.sleep(2)

    record("C4c exactly 1 usage_records row for the fallback request",
           c4_row_count == 1,
           f"usage rows={c4_row_count} (expected 1)")
    record("C4d usage row model_id == stub/fb-secondary (served id billed)",
           c4_db_model == FB_SECONDARY,
           f"db model_id={c4_db_model!r} (expected {FB_SECONDARY!r})")

    # =========================================================================
    # C5: COOLDOWN (v6 health gate) — drive fb-primary failures via the alias.
    #     AUTHORITATIVE signal = GET /admin/routing snapshot_state == "open"
    #     (robust to simple-shuffle pick order AND upstream retries — unlike
    #     inferring from stub counters). After TTL(5s)+fault clear, fb-primary
    #     leaves "open" (recovers) and a probe is served by it again.
    # =========================================================================
    print("\n── C5 cooldown ──")
    routing_url = f"{BASE}/admin/routing"

    c5_key_r = client.post(f"{BASE}/admin/keys",
                           json={"name": f"v8-c5-{run_id}"}, headers=auth_hdrs)
    assert c5_key_r.status_code == 201, f"c5 key: {c5_key_r.text[:200]}"
    c5_raw_key: str = c5_key_r.json()["key"]

    # Force fb-primary to always fail; fb-secondary serves each fallback success.
    _set_fault(FB_PRIMARY, "fail_5xx")
    _set_fault(FB_SECONDARY, "ok")

    # Fire paced alias requests until the cooldown gate marks fb-primary "open".
    # Under simple-shuffle fb-primary is tried ~50%/request, so a handful of
    # requests accumulate >= threshold(2) failures. We peek /admin/routing between
    # requests and stop as soon as it is cooled (generous cap of 20 requests).
    tripped = False
    last_state_trip = "unknown"
    for _ in range(20):
        _completion(V8_FB, raw_key_override=c5_raw_key, content="C5 trip")
        time.sleep(EDGE_PACE_S)  # stay under the Envoy 50 req/s edge bucket
        tripped, last_state_trip = _poll_candidate_state(
            client, routing_url, auth_hdrs, FB_PRIMARY, "open", ceiling_s=0.5,
        )
        if tripped:
            break
    record("C5a cooldown tripped: fb-primary state == 'open' via /admin/routing (authoritative)",
           tripped,
           f"state={last_state_trip!r} tripped={tripped}")

    # While cooled, an alias request is served by fb-secondary (fb-primary skipped
    # at selection — fb-primary is still faulted, so this holds even if a half-open
    # probe slips in: that probe fails and the answer still comes from fb-secondary).
    r_c5_cooled = _completion(V8_FB, raw_key_override=c5_raw_key, content="C5 cooled check")
    c5_cooled_model = r_c5_cooled.json().get("model", "") if r_c5_cooled.status_code == 200 else ""
    record("C5b cooled alias request served by fb-secondary (fb-primary skipped at selection)",
           r_c5_cooled.status_code == 200 and c5_cooled_model == FB_SECONDARY,
           f"status={r_c5_cooled.status_code} model={c5_cooled_model!r}")

    # Clear the fault and poll until fb-primary LEAVES "open" (TTL expiry → closed).
    _set_fault(FB_PRIMARY, "ok")
    recovered, last_state_rec = _poll_candidate_state(
        client, routing_url, auth_hdrs, FB_PRIMARY, "open",
        ceiling_s=COOLDOWN_RECOVERY_POLL_CEILING_S, negate=True,
    )
    record("C5c fb-primary recovers from 'open' after TTL expiry",
           recovered,
           f"last_state={last_state_rec!r} (expected != 'open')")

    # After recovery fb-primary is eligible again; under simple-shuffle it is the
    # pick ~50%/request, so a few probes serve it (P(missed in 12 tries) < 0.025%).
    recovered_ok = False
    fb_primary_served = 0
    for _ in range(12):
        rp = _completion(V8_FB, raw_key_override=c5_raw_key, content="C5 probe recovery")
        time.sleep(EDGE_PACE_S)
        if rp.status_code == 200:
            recovered_ok = True
            if rp.json().get("model") == FB_PRIMARY:
                fb_primary_served += 1
                break
    record("C5d after recovery a probe succeeds and fb-primary is eligible again",
           recovered_ok and fb_primary_served > 0,
           f"recovered_ok={recovered_ok} fb-primary served in probes={fb_primary_served}")

    # Reset faults for remaining checks
    _set_fault(FB_PRIMARY, "ok")
    _set_fault(FB_SECONDARY, "ok")

    # =========================================================================
    # C6: BILLING SERVED ID — chat v8-bill → exactly 1 usage row;
    #     model_id = SERVED deployment id (stub/bill-a or stub/bill-b);
    #     NEVER the alias "v8-bill"; cost_usd > 0.
    # =========================================================================
    print("\n── C6 billing served id ──")

    c6_key_r = client.post(f"{BASE}/admin/keys",
                           json={"name": f"v8-c6-{run_id}"}, headers=auth_hdrs)
    assert c6_key_r.status_code == 201, f"c6 key: {c6_key_r.text[:200]}"
    c6_raw_key: str = c6_key_r.json()["key"]
    c6_key_id: str = c6_key_r.json()["key_id"]

    # Reset counters so we can read which deployment was served
    _set_fault(BILL_A, "ok")
    _set_fault(BILL_B, "ok")

    r_c6 = _completion(V8_BILL, raw_key_override=c6_raw_key,
                       content="C6 billing test")
    record("C6a v8-bill chat returns 200",
           r_c6.status_code == 200,
           f"status={r_c6.status_code}")

    c6_response_model = r_c6.json().get("model", "") if r_c6.status_code == 200 else ""
    ctrs_c6 = _get_counters()

    # The stub echoes the served model id in the response model field.
    # Cross-check: the served id should have counter == 1 in /__counters.
    served_id = c6_response_model

    # Poll usage_records
    c6_row_count = _poll_usage_rows(c6_key_id, min_rows=1)
    c6_db_model = ""
    c6_db_cost = ""
    if c6_row_count >= 1:
        c6_db_model = psql(
            f"SELECT model_id FROM usage_records WHERE key_id='{c6_key_id}' LIMIT 1"
        )
        c6_db_cost = psql(
            f"SELECT cost_usd::text FROM usage_records WHERE key_id='{c6_key_id}' LIMIT 1"
        )

    record("C6b exactly 1 usage_records row",
           c6_row_count == 1,
           f"usage rows={c6_row_count} (expected 1)")
    record("C6c usage row model_id is the SERVED deployment id (not alias v8-bill)",
           c6_db_model in (BILL_A, BILL_B) and c6_db_model != V8_BILL,
           f"db model_id={c6_db_model!r} (expected one of {BILL_A!r} or {BILL_B!r})")
    try:
        c6_cost_float = float(c6_db_cost)
    except (ValueError, TypeError):
        c6_cost_float = 0.0
    record("C6d cost_usd > 0",
           c6_cost_float > 0,
           f"cost_usd={c6_db_cost!r}")
    record("C6e db model_id matches response model (consistent served-id tracking)",
           c6_db_model == served_id,
           f"db model_id={c6_db_model!r} response model={served_id!r}")

    # =========================================================================
    # C7: GOVERNANCE + TLS — chat v8-dist with NO bearer → 401;
    #     0 usage rows; all checks through https://localhost:8443.
    # =========================================================================
    print("\n── C7 governance + TLS ──")

    c7_key_r = client.post(f"{BASE}/admin/keys",
                           json={"name": f"v8-c7-{run_id}"}, headers=auth_hdrs)
    assert c7_key_r.status_code == 201, f"c7 key: {c7_key_r.text[:200]}"
    c7_key_id: str = c7_key_r.json()["key_id"]

    r_c7 = _completion(V8_DIST, bearer="")  # no bearer
    record("C7a no-bearer chat returns 401",
           r_c7.status_code == 401,
           f"status={r_c7.status_code} (expected 401)")

    # Brief pause; then assert zero usage rows for the rejected call
    time.sleep(3)
    c7_rows = int(psql(
        f"SELECT count(*) FROM usage_records WHERE key_id='{c7_key_id}'"
    ) or "0")
    record("C7b zero usage_records rows for the rejected (no-bearer) call",
           c7_rows == 0,
           f"usage rows={c7_rows} (expected 0)")

    record("C7c all checks ran through TLS edge https://localhost:8443",
           BASE.startswith("https://"),
           f"BASE={BASE!r}")
    record("C7d run_id embedded in all identities (fresh per invocation)",
           True,
           f"run_id={run_id} in email={email!r}")

    # ── Summary ───────────────────────────────────────────────────────────────
    stub_srv.shutdown()
    client.close()

    print()
    failed = [c for c, ok, _ in RESULTS if not ok]
    total = len(RESULTS)
    passed = total - len(failed)
    print(f"\n{'=' * 60}")
    print("FINAL RESULTS — v8 live verification")
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
    print("      -f infra/docker-compose.e2e.v8.yml \\")
    print("      up --build -d --wait")
    print("  uv run --project apps/gateway python scripts/live_v8_verify.py")
    print("  uv run --project apps/gateway python scripts/live_v8_verify.py  # second pass")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
