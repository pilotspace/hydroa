#!/usr/bin/env python3
"""v12 live double-pass verification — exact Gemini-embed billing, non-chat soft alert,
empty-key boot guard.

Proves, end-to-end through https://localhost:8443, that a tenant can:
  C1  Exact Gemini-embed token billing: a /v1/embeddings call (provider=google) bills
      usage on the EXACT token count returned by :countTokens (42), NOT ceil(chars/4).
  C2  Non-chat soft-budget alert: an embeddings request on a key whose per-key Redis
      spend counter is seeded past its soft_budget fires exactly ONE soft_budget_exceeded
      alert_events row (idempotent dedupe_key); a repeat request does NOT add a second row;
      both requests still return 200 (advisory, not 402).
  C3  Empty-key boot guard: starting the gateway with GATEWAY_OPENROUTER_API_KEY=""
      exits non-zero (EmptyUpstreamKeyError); an absent key boots normally (control).
  C4  Governance intact: a bad API key on /v1/embeddings is rejected 401 with no
      usage_records row written.

Operator-run; NO production source change. Starts scripts/v12_embed_stub.py in a daemon
thread, seeds 1 google-provider embedding model + pricing, restarts the gateway so the
lifespan provider_resolver.refresh() reads them, then runs the checks with a fresh
tenant+key. run_id changes on every invocation (int(time.time())).

Double-pass close rule: run twice in sequence; both must exit 0.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.environ.get("SMOKE_BASE", "https://localhost:8443")

STUB_HOST = "127.0.0.1"
STUB_PORT = 9926

# The single google embed model seeded for v12 checks.
V12_GOOGLE_EMBED = "v12-google-embed"

# EXACT token count the stub's :countTokens returns — C1 asserts usage == this.
_EXACT_TOKENS = 42

# Input whose ceil(chars/4) != 42 — ensures the EXACT path is exercised, not the fallback.
# "hello world" → 11 chars → ceil(11/4) = 3.  42 ≠ 3, so C1 catches the fallback.
_EMBED_INPUT = "hello world"

EDGE_PACE_S = 0.05
EDGE_SETTLE_S = 1.5

PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"
REDIS_CONTAINER = "hydroa-e2e-redis-1"

run_id = int(time.time())
RESULTS: list[tuple[str, bool, str]] = []

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import v12_embed_stub  # noqa: E402,I001 — after sys.path setup


# ---------------------------------------------------------------------------
# Results helper
# ---------------------------------------------------------------------------


def record(criterion: str, ok: bool, note: str) -> None:
    RESULTS.append((criterion, ok, note))
    print(f"{'PASS' if ok else 'FAIL'}  {criterion}: {note}", flush=True)


# ---------------------------------------------------------------------------
# DB helper — docker exec psql (same pattern as live_v9/v10/v11_verify.py)
# ---------------------------------------------------------------------------


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", "gateway", "-d", "gateway_e2e",
         "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


# ---------------------------------------------------------------------------
# Redis helper — docker exec redis-cli (for spend counter pre-seeding)
# ---------------------------------------------------------------------------


def redis_set(key: str, value: str) -> None:
    """SET a Redis key to value via docker exec redis-cli."""
    subprocess.run(
        ["docker", "exec", REDIS_CONTAINER, "redis-cli", "SET", key, value],
        capture_output=True, text=True, check=True, timeout=10,
    )


# ---------------------------------------------------------------------------
# Stack helpers
# ---------------------------------------------------------------------------


def _wait_stub_healthy(timeout: float = 10.0) -> None:
    """Poll http://127.0.0.1:9926/__health until 200 or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"http://{STUB_HOST}:{STUB_PORT}/__health", method="GET"
            )
            if urllib.request.urlopen(req, timeout=3).status == 200:
                print(f"  [stub] healthy on {STUB_HOST}:{STUB_PORT}")
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"v12 embed stub did not become healthy within {timeout}s")


def _seed_v12_models() -> None:
    """INSERT one google-provider embedding model + pricing_snapshot via docker-exec psql.

    Model (per §3 CONTRACT, FROZEN):
      v12-google-embed   provider=google  modality=embedding

    Pricing: per_token with prompt_usd_per_token>0 so cost_usd>0 for billing assertions.
    ON CONFLICT (id) DO UPDATE SET active=true — idempotent across double-pass runs.

    Seeded BEFORE gateway restart so the lifespan provider_resolver.refresh() reads
    active=true rows. No catalog sync is triggered; the restart is the refresh mechanism.
    """
    _ts = "now(), now()"
    model_rows = [
        f"('{V12_GOOGLE_EMBED}', 'v12 Google embedding', 8192, true,"
        f" 'embedding', 'google', {_ts})",
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
        f"(gen_random_uuid(), '{V12_GOOGLE_EMBED}', {_price})",
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
    print("  [seed] 1 v12 google embedding model + pricing_snapshot seeded (active=true)")


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
        "-f", "infra/docker-compose.e2e.v12.yml",
        "restart", "gateway",
    ]
    subprocess.run(compose_cmd, capture_output=True, text=True, check=True, timeout=120)
    print("  [gateway] restart triggered; polling health ...")

    # Poll the gateway internal health endpoint via docker exec (avoids TLS complexity
    # before the edge is ready; mirrors live_v9/v10/v11_verify._restart_gateway_and_wait)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["docker", "exec", GW_CONTAINER, "python", "-c",
                 "import urllib.request; "
                 "urllib.request.urlopen('http://localhost:8000/health', timeout=3)"],
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


def _poll_usage_tokens(key_id: str, *, ceiling_s: float = 30.0) -> tuple[int, int, str]:
    """Poll usage_records until at least 1 row exists for key_id.

    Returns (prompt_tokens, completion_tokens, cost_usd) from the first row.
    Polls every 2 s up to ceiling_s. Usage flushing is async — always poll.
    """
    deadline = time.time() + ceiling_s
    while time.time() < deadline:
        if int(psql(f"SELECT count(*) FROM usage_records WHERE key_id='{key_id}'") or "0") >= 1:
            raw = psql(
                "SELECT prompt_tokens::text || ',' || completion_tokens::text || ',' "
                f"|| cost_usd::text FROM usage_records WHERE key_id='{key_id}' "
                "ORDER BY created_at ASC LIMIT 1"
            )
            parts = raw.split(",")
            try:
                return int(parts[0]), int(parts[1]), parts[2]
            except (IndexError, ValueError):
                return 0, 0, ""
        time.sleep(2)
    return 0, 0, ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    import httpx

    # ── Start stub ──────────────────────────────────────────────────────────
    stub_srv = v12_embed_stub.make_stub_server()
    v12_embed_stub.start_stub_in_thread(stub_srv)
    print(f"v12 embed stub   :{STUB_PORT}  (localhost only)\nrun_id={run_id}")

    server_addr = stub_srv.server_address[0]
    if server_addr != "127.0.0.1":
        print(f"HARD-STOP: stub bound to {server_addr!r} — ERR_SECURITY_VIOLATION", file=sys.stderr)
        sys.exit(1)
    print(f"  [security] stub address asserted: {server_addr}:{STUB_PORT} (loopback only) OK")

    _wait_stub_healthy()
    _seed_v12_models()
    _restart_gateway_and_wait()
    time.sleep(EDGE_SETTLE_S)

    client = httpx.Client(verify=False, timeout=90, follow_redirects=True)

    # ── Signup + login (fresh tenant per run_id) ───────────────────────────
    email = f"v12-verify-{run_id}@live.io"
    password = "v12-verify-password-live"  # noqa: S105 — test harness password, not a secret
    r_signup = client.post(
        f"{BASE}/admin/auth/signup",
        json={"tenant_name": f"V12VerifyCo-{run_id}", "email": email, "password": password},
    )
    assert r_signup.status_code == 201, f"signup: {r_signup.status_code} {r_signup.text[:200]}"
    jwt_token = client.post(
        f"{BASE}/admin/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    auth_hdrs = {"Authorization": f"Bearer {jwt_token}"}

    def _new_key(label: str, *, soft_budget_usd: str | None = None) -> tuple[str, str]:
        body: dict = {"name": f"v12-{label}-{run_id}"}
        if soft_budget_usd is not None:
            body["soft_budget_usd"] = soft_budget_usd
        r = client.post(f"{BASE}/admin/keys", json=body, headers=auth_hdrs)
        assert r.status_code == 201, f"{label} key: {r.text[:200]}"
        return r.json()["key"], r.json()["key_id"]

    def _post_embeddings(model: str, key: str, inp: str) -> httpx.Response:
        time.sleep(EDGE_PACE_S)
        return client.post(
            f"{BASE}/v1/embeddings",
            json={"model": model, "input": inp},
            headers={"Authorization": f"Bearer {key}"},
        )

    # ── C1 Exact Gemini-embed token billing ────────────────────────────────
    print("\n── C1 Exact Gemini-embed token billing ──")
    c1_key, c1_id = _new_key("C1")
    # Use input whose ceil(chars/4) != 42 to prove :countTokens path, not fallback.
    # "hello world" → 11 chars → ceil(11/4)=3; stub returns 42 → must bill 42.
    r_c1 = _post_embeddings(V12_GOOGLE_EMBED, c1_key, _EMBED_INPUT)
    if r_c1.status_code == 200:
        record("C1a embeddings request returned 200", True, f"status={r_c1.status_code}")
    else:
        record("C1a embeddings request returned 200", False,
               f"status={r_c1.status_code} {r_c1.text[:160]!r}")

    c1_pt, _c1_ct, _c1_cost = _poll_usage_tokens(c1_id)
    # prompt_tokens == _EXACT_TOKENS (42): stub countTokens path was taken.
    # If fallback: ceil("hello world" chars / 4) = 3 — clearly != 42 → FAIL.
    record(
        f"C1b billed exact token count from :countTokens (prompt={_EXACT_TOKENS})",
        c1_pt == _EXACT_TOKENS,
        f"prompt_tokens={c1_pt} (expected {_EXACT_TOKENS}; fallback ceil(11/4)=3 → FAIL)",
    )
    c1_model = psql(
        f"SELECT model_id FROM usage_records WHERE key_id='{c1_id}' "
        "ORDER BY created_at ASC LIMIT 1"
    )
    record("C1c usage row model_id == v12-google-embed",
           c1_model == V12_GOOGLE_EMBED, f"model_id={c1_model!r}")

    # ── C2 Non-chat soft-budget alert ──────────────────────────────────────
    print("\n── C2 Non-chat soft-budget alert ──")
    # Create a key with soft_budget_usd="0.00" (crossed immediately once any spend exists)
    c2_key, c2_id = _new_key("C2", soft_budget_usd="0.00")

    # Seed the per-key Redis spend counter ABOVE the soft_budget (0.00) so that the
    # governance check fires on the NEXT request.
    # Key pattern: usage:spend:key:{key_id}:{YYYYMM} (RedisBudgetGuard)
    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    spend_redis_key = f"usage:spend:key:{c2_id}:{yyyymm}"
    redis_set(spend_redis_key, "9999.99")
    print("  [C2] seeded Redis spend counter past soft_budget_usd=0.00")

    # First request — should succeed (200) but fire the alert
    r_c2a = _post_embeddings(V12_GOOGLE_EMBED, c2_key, _EMBED_INPUT)
    record("C2a first embeddings request (past soft budget) returns 200",
           r_c2a.status_code == 200,
           f"status={r_c2a.status_code} (soft budget is advisory — must NOT 402)")

    # Give the fire-and-forget alert task time to commit
    time.sleep(2)

    alert_count_1 = int(
        psql(
            "SELECT count(*) FROM alert_events "
            f"WHERE event_type='soft_budget_exceeded' AND key_id='{c2_id}'"
        ) or "0"
    )
    record("C2b exactly 1 soft_budget_exceeded alert_events row after first request",
           alert_count_1 == 1,
           f"alert_count={alert_count_1} (expected 1)")

    # Repeat request — dedupe_key must prevent a second row
    r_c2b = _post_embeddings(V12_GOOGLE_EMBED, c2_key, _EMBED_INPUT)
    record("C2c repeat embeddings request (same month) also returns 200",
           r_c2b.status_code == 200,
           f"status={r_c2b.status_code}")

    time.sleep(2)

    alert_count_2 = int(
        psql(
            "SELECT count(*) FROM alert_events "
            f"WHERE event_type='soft_budget_exceeded' AND key_id='{c2_id}'"
        ) or "0"
    )
    record("C2d alert_events count still 1 after repeat (idempotent dedupe_key)",
           alert_count_2 == 1,
           f"alert_count={alert_count_2} (expected 1; dedupe_key soft_budget:{c2_id}:{yyyymm})")

    # ── C3 Empty-key boot guard ────────────────────────────────────────────
    print("\n── C3 Empty-key boot guard ──")
    # Launch a one-shot gateway process with GATEWAY_OPENROUTER_API_KEY="" via docker compose
    # run --rm. The gateway's create_app() → validate_upstream_keys() raises
    # EmptyUpstreamKeyError → non-zero exit. We assert exit != 0 only.
    boot_guard_cmd = [
        "docker", "compose",
        "-f", "infra/docker-compose.e2e.yml",
        "-f", "infra/docker-compose.e2e.v4.yml",
        "-f", "infra/docker-compose.e2e.v5.yml",
        "-f", "infra/docker-compose.e2e.v6.yml",
        "-f", "infra/docker-compose.e2e.v12.yml",
        "run", "--rm",
        "-e", "GATEWAY_OPENROUTER_API_KEY=",
        "gateway",
        "python", "-c",
        (
            "from gateway.core.config import validate_upstream_keys; "
            "validate_upstream_keys()"
        ),
    ]
    try:
        result_empty = subprocess.run(
            boot_guard_cmd,
            capture_output=True, text=True, timeout=60,
        )
        c3_empty_nonzero = result_empty.returncode != 0
        c3_note = f"exit={result_empty.returncode}"
        if not c3_empty_nonzero:
            c3_note += f" stderr={result_empty.stderr[:200]!r}"
    except subprocess.TimeoutExpired:
        # A gateway that hangs is also a failure — boot guard should be instant
        c3_empty_nonzero = False
        c3_note = "timed out (expected fast exit)"
    except Exception as exc:
        c3_empty_nonzero = False
        c3_note = f"exception: {exc}"

    record("C3a GATEWAY_OPENROUTER_API_KEY='' causes non-zero exit (EmptyUpstreamKeyError)",
           c3_empty_nonzero, c3_note)

    # Control: absent key boots without error.
    # Run validate_upstream_keys() in a fresh subprocess with the key popped from environ —
    # no Docker needed for the control case (tests the same code path, faster).
    result_absent = subprocess.run(
        [sys.executable, "-c",
         "import sys, os; "
         "os.environ.pop('GATEWAY_OPENROUTER_API_KEY', None); "
         "sys.path.insert(0, 'apps/gateway/src'); "
         "from gateway.core.config import validate_upstream_keys; "
         "validate_upstream_keys(); "
         "print('absent-key control: OK')"],
        capture_output=True, text=True, timeout=15,
    )
    c3_absent_ok = result_absent.returncode == 0
    c3_absent_note = (
        f"exit={result_absent.returncode} "
        f"stdout={result_absent.stdout.strip()[:80]!r}"
    )
    record("C3b absent GATEWAY_OPENROUTER_API_KEY boots without error (control)",
           c3_absent_ok, c3_absent_note)

    # ── C4 Governance intact ───────────────────────────────────────────────
    print("\n── C4 Governance intact (bad API key → 401, no usage row) ──")
    bad_key = "sk-not-a-real-v12-key"
    # Snapshot the TOTAL usage_records count immediately before the bad-key request, so the
    # bad-key request's effect is isolated by a delta (the e2e DB is shared across runs — a
    # prior pass's legitimate rows must NOT count against this check).
    c4_before = int(psql("SELECT count(*) FROM usage_records") or "0")
    time.sleep(EDGE_PACE_S)
    r_c4 = client.post(
        f"{BASE}/v1/embeddings",
        json={"model": V12_GOOGLE_EMBED, "input": _EMBED_INPUT},
        headers={"Authorization": f"Bearer {bad_key}"},
    )
    record("C4a bad API key rejected 401/403",
           r_c4.status_code in (401, 403),
           f"status={r_c4.status_code}")

    time.sleep(2)
    # The bad key never authenticates → governance rejects before billing → the total
    # usage_records count must be UNCHANGED by the bad-key request (delta == 0).
    c4_after = int(psql("SELECT count(*) FROM usage_records") or "0")
    c4_delta = c4_after - c4_before
    record("C4b no usage_records row written for bad key",
           c4_delta == 0,
           f"delta={c4_delta} (before={c4_before} after={c4_after}; expected 0 — "
           "governance rejected before billing)")

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for crit, ok, note in RESULTS:
        if not ok:
            print(f"  FAIL  {crit}: {note}")
    print(f"v12 live verify: {passed}/{total} checks passed (run_id={run_id})")
    stub_srv.shutdown()
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
