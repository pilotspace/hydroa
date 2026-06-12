#!/usr/bin/env python3
"""Live v6 exit-criteria verification — all through the Envoy TLS edge.

# SKELETON — BUILD PHASE COMPLETES THIS FILE
# Status: DRAFT as of v6-live-verify spec phase. The structure, constants,
# check mapping (C1–C6), double-pass rule, and helper signatures are final
# (frozen in TASK.md §3). The check bodies need completion in BUILD.

Operator-run (requires a real key + the e2e TLS stack with v4+v5+v6 overlays):
    export GATEWAY_OPENROUTER_API_KEY=sk-or-...
    python scripts/v6_fault_stub.py &  # or let this script start it
    docker compose \\
        -f infra/docker-compose.e2e.yml \\
        -f infra/docker-compose.e2e.v4.yml \\
        -f infra/docker-compose.e2e.v5.yml \\
        -f infra/docker-compose.e2e.v6.yml \\
        up --build -d --wait
    uv run --project apps/gateway python scripts/live_v6_verify.py

Criteria covered (v6 MILESTONE.md exit criteria):
  C1 retry-policy:    pre-stream 5xx retried within budget (max_retries=2);
                      client gets 200; exactly ONE ledger row for the served candidate.
  C2 model-fallbacks: alias v6-alias — stub/primary fails → stub/fallback serves;
                      ledger row model == "stub/fallback" + pricing snapshot present.
  C3 cooldown-circuit: stub/primary tripped past threshold (2 consecutive failures);
                       state "open" visible via GET /admin/routing; half-open probe
                       recovery after TTL (5 s); transitions in metrics.
  C4 routing-admin:   GET /admin/routing returns correct shape, tenant-authenticated,
                      secrets-free; candidates include stub/primary + stub/fallback.
  C5 mid-stream:      stream_cut behavior → gateway closes stream; no second attempt;
                      single usage_records row.
  C6 TLS + isolation: all checks through https://localhost:8443; every identity carries
                      run_id; double-pass rule documented (orchestrator runs twice).

Shape sources per check:
  C1  retry-policy:
      apps/gateway/tests/retry_policy/test_retry_policy.py (retry scenarios)
      apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py
  C2  model-fallbacks:
      apps/gateway/tests/model_fallbacks/ (fallback scenarios)
  C3  cooldown-circuit:
      apps/gateway/tests/cooldown_circuit/ (circuit scenarios)
      apps/gateway/src/gateway/proxy/infrastructure/redis_cooldown_gate.py
  C4  routing-admin (GET /admin/routing shape):
      .add/tasks/routing-admin/TASK.md §3 FROZEN contract
      apps/gateway/tests/routing_admin/test_routing_admin.py
  C5  mid-stream:
      .add/milestones/v6/MILESTONE.md §"Streaming is the hard boundary"
  C6  TLS + isolation:
      infra/docker-compose.e2e.v6.yml (overlay with TLS edge :8443)
      scripts/v6_fault_stub.py (stub on :9920)

Exit codes: 0 = all criteria PASS · 1 = failure · 2 = key absent

DOUBLE-PASS CLOSE RULE (§3 contract):
  The orchestrator runs this script twice in sequence. Both runs must exit 0.
  This script does NOT enforce the double-pass — it documents the re-run command.
  run_id changes on every invocation; all identities are fresh. This is the
  isolation proof equivalent to v5's C6 re-runnability check.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE = os.environ.get("SMOKE_BASE", "https://localhost:8443")
CA = os.environ.get("E2E_CA_CERT", "infra/envoy/certs/dev-ca.pem")

# Stub server — started by this script before any check runs
STUB_HOST = "127.0.0.1"
STUB_PORT = 9920

# Model group alias wired in the v6 overlay
V6_ALIAS = "v6-alias"
STUB_PRIMARY = "stub/primary"
STUB_FALLBACK = "stub/fallback"

# Cooldown TTL configured in the v6 overlay (GATEWAY_COOLDOWN_TTL_S=5)
COOLDOWN_TTL_S = 5
# Poll ceilings — generous to absorb TTL timing variance (see §3 [part] flag)
COOLDOWN_TRIP_POLL_CEILING_S = 10.0
COOLDOWN_RECOVERY_POLL_CEILING_S = 15.0

PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"

# Per-run unique ID — embeds in every tenant email and key name (C6 isolation)
run_id = int(time.time())

RESULTS: list[tuple[str, bool, str]] = []

# ---------------------------------------------------------------------------
# Stub server import (started below in main())
# ---------------------------------------------------------------------------

# Add scripts/ dir to path so v6_fault_stub can be imported
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import v6_fault_stub  # noqa: E402 — after sys.path setup


# ---------------------------------------------------------------------------
# Results helper
# ---------------------------------------------------------------------------

def record(criterion: str, ok: bool, note: str) -> None:
    RESULTS.append((criterion, ok, note))
    status = "PASS" if ok else "FAIL"
    print(f"{status}  {criterion}: {note}", flush=True)


# ---------------------------------------------------------------------------
# DB helper — docker exec psql (same pattern as live_v5_verify.py)
# ---------------------------------------------------------------------------

def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", "gateway", "-d", "gateway_e2e",
         "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


# ---------------------------------------------------------------------------
# Internal metrics helper — read via docker exec (edge blocks /internal)
# ---------------------------------------------------------------------------

def _read_internal_metrics() -> str:
    """Fetch /internal/metrics from within the gateway container."""
    result = subprocess.run(
        ["docker", "exec", GW_CONTAINER, "python", "-c",
         "import urllib.request; print(urllib.request.urlopen("
         "'http://localhost:8000/internal/metrics', timeout=10).read().decode())"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


# ---------------------------------------------------------------------------
# Fault control helpers
# ---------------------------------------------------------------------------

def _set_fault(model_id: str, behavior: object) -> None:
    """Configure fault table via stub control endpoint."""
    import urllib.request
    payload = json.dumps({"model": model_id, "behavior": behavior}).encode()
    req = urllib.request.Request(
        f"http://{STUB_HOST}:{STUB_PORT}/__faults",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5)


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


# ---------------------------------------------------------------------------
# Poll helper for /admin/routing state
# ---------------------------------------------------------------------------

def _poll_candidate_state(
    client: object,
    url: str,
    auth_hdrs: dict[str, str],
    model_id: str,
    target_state: str,
    *,
    ceiling_s: float,
    interval_s: float = 1.0,
    negate: bool = False,
) -> tuple[bool, str]:
    """Poll GET /admin/routing until candidate reaches (or leaves) target_state.

    Returns (success, last_observed_state).
    negate=True: poll until state != target_state (used for recovery detection).

    SKELETON: BUILD phase fills this in using httpx client.
    """
    # SKELETON — BUILD phase implements this using the httpx client passed in
    raise NotImplementedError("SKELETON: _poll_candidate_state not yet implemented — BUILD phase")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not os.environ.get("GATEWAY_OPENROUTER_API_KEY", "").strip():
        print("REFUSED: GATEWAY_OPENROUTER_API_KEY unset.", file=sys.stderr)
        sys.exit(2)

    import httpx

    # ── Start fault stub ──────────────────────────────────────────────────────
    stub_srv = v6_fault_stub.make_stub_server()
    v6_fault_stub.start_stub_in_thread(stub_srv)
    print(f"v6 fault stub       :{STUB_PORT}  (localhost only)")
    print(f"run_id={run_id}")

    # ── TLS client ────────────────────────────────────────────────────────────
    ca = CA if os.path.exists(CA) else os.path.join("..", "..", CA)
    tls_verify: str | bool = ca if os.path.exists(ca) else False
    if not os.path.exists(ca if isinstance(ca, str) else ""):
        print(f"  [tls] CA cert not found at {ca!r}, falling back to verify=False")
        tls_verify = False

    client = httpx.Client(verify=tls_verify, timeout=90, follow_redirects=True)

    # ── Catalog sync ──────────────────────────────────────────────────────────
    _catalog_sync()

    # ── Arrange: base tenant + JWT ────────────────────────────────────────────
    email = f"v6-verify-{run_id}@live.io"
    password = "v6-verify-password-live"
    r = client.post(f"{BASE}/admin/auth/signup",
                    json={"tenant_name": f"V6VerifyCo-{run_id}",
                          "email": email, "password": password})
    assert r.status_code == 201, f"signup: {r.status_code} {r.text[:200]}"
    tenant_id: str = r.json()["tenant_id"]
    jwt_token = client.post(f"{BASE}/admin/auth/login",
                            json={"email": email, "password": password}).json()["access_token"]
    auth_hdrs = {"Authorization": f"Bearer {jwt_token}"}
    print(f"base tenant_id={tenant_id}")

    # Create a key for completion requests
    key_r = client.post(f"{BASE}/admin/keys",
                        json={"name": f"v6-key-{run_id}"}, headers=auth_hdrs)
    assert key_r.status_code == 201, f"create_key: {key_r.text[:200]}"
    raw_key: str = key_r.json()["key"]
    key_id: str = key_r.json()["key_id"]

    def _completion(model: str, stream: bool = False, content: str = "Say OK.") -> httpx.Response:
        return client.post(
            f"{BASE}/v1/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": content}],
                  "max_tokens": 8, "stream": stream},
            headers={"Authorization": f"Bearer {raw_key}"},
        )

    # ── Pre-check: reset fault table to clean state ───────────────────────────
    _set_fault(STUB_PRIMARY, "ok")
    _set_fault(STUB_FALLBACK, "ok")

    # =========================================================================
    # C1: Pre-stream 5xx retried within budget; exactly ONE ledger row
    # =========================================================================
    # SKELETON: BUILD phase implements the check body.
    # Shape: configure stub/primary to fail_n=2; send completion for v6-alias;
    #        assert 200; wait for flusher; assert exactly 1 usage_records row.
    # =========================================================================
    print("\n── C1 retry-policy ──")

    # TODO BUILD: implement C1 check body
    # _set_fault(STUB_PRIMARY, {"fail_n": 2})
    # _set_fault(STUB_FALLBACK, "ok")
    # r_c1 = _completion(V6_ALIAS)
    # record("C1a retry: 200 after 5xx retries", r_c1.status_code == 200, ...)
    # ... wait for flusher; check ledger row count == 1 ...
    record("C1 SKELETON", False, "BUILD phase: not yet implemented")

    # =========================================================================
    # C2: Alias fallback to next candidate; ledger carries served model
    # =========================================================================
    # SKELETON: BUILD phase implements the check body.
    # Shape: stub/primary=fail_5xx, stub/fallback=ok; send completion for v6-alias;
    #        assert 200; assert response model == "stub/fallback"; check ledger.
    # =========================================================================
    print("\n── C2 model-fallbacks ──")

    # TODO BUILD: implement C2 check body
    record("C2 SKELETON", False, "BUILD phase: not yet implemented")

    # =========================================================================
    # C3: Cooldown trip + half-open recovery
    # =========================================================================
    # SKELETON: BUILD phase implements the check body.
    # Shape: force 2 consecutive failures on stub/primary;
    #        poll GET /admin/routing until stub/primary state == "open";
    #        assert fallback serves next request; wait TTL; poll for recovery;
    #        restore stub/primary; assert probe succeeds; check metrics counter.
    # Poll ceiling: COOLDOWN_TRIP_POLL_CEILING_S=10, COOLDOWN_RECOVERY_POLL_CEILING_S=15
    # Timing risk: mitigated by poll (see §3 [part] flag).
    # =========================================================================
    print("\n── C3 cooldown-circuit ──")

    # TODO BUILD: implement C3 check body
    record("C3 SKELETON", False, "BUILD phase: not yet implemented")

    # =========================================================================
    # C4: GET /admin/routing correct shape, authenticated, secrets-free
    # =========================================================================
    # SKELETON: BUILD phase implements the check body.
    # Shape: GET /admin/routing with auth_hdrs; assert 200; assert all four
    #        top-level keys present; assert stub/primary + stub/fallback in candidates;
    #        assert openrouter_api_key value not in json.dumps(response body).
    # Source: routing-admin TASK.md §3 FROZEN contract (read-only consumer here).
    # =========================================================================
    print("\n── C4 routing-admin ──")

    # TODO BUILD: implement C4 check body
    record("C4 SKELETON", False, "BUILD phase: not yet implemented")

    # =========================================================================
    # C5: Mid-stream failure; no retry/fallback; single ledger row
    # =========================================================================
    # SKELETON: BUILD phase implements the check body.
    # Shape: configure stub/primary to "stream_cut"; send streaming completion;
    #        assert stream closes after first chunk; assert single usage_records row.
    # =========================================================================
    print("\n── C5 mid-stream boundary ──")

    # TODO BUILD: implement C5 check body
    record("C5 SKELETON", False, "BUILD phase: not yet implemented")

    # =========================================================================
    # C6: TLS edge + run_id isolation
    # =========================================================================
    # All requests use BASE = https://localhost:8443 (TLS edge).
    # run_id is embedded in every identity created above.
    # Double-pass: the orchestrator runs this script twice; both must exit 0.
    # =========================================================================
    print("\n── C6 TLS edge + isolation ──")

    record("C6a all checks through TLS edge https://localhost:8443",
           BASE.startswith("https://"),
           f"BASE={BASE!r}")
    record("C6b run_id embedded in all identities",
           True,
           f"run_id={run_id} in: email={email!r}, key_name=v6-key-{run_id}")

    # ── Summary ───────────────────────────────────────────────────────────────
    stub_srv.shutdown()
    client.close()

    print()
    failed = [c for c, ok, _ in RESULTS if not ok]
    total = len(RESULTS)
    passed = total - len(failed)
    print(f"\n{'=' * 60}")
    print("FINAL RESULTS — v6 live verification")
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
    print("Re-run command (operator runs twice for double-pass close rule):")
    print("  docker compose \\")
    print("      -f infra/docker-compose.e2e.yml \\")
    print("      -f infra/docker-compose.e2e.v4.yml \\")
    print("      -f infra/docker-compose.e2e.v5.yml \\")
    print("      -f infra/docker-compose.e2e.v6.yml \\")
    print("      up -d --wait")
    print("  uv run --project apps/gateway python scripts/live_v6_verify.py")
    print("  uv run --project apps/gateway python scripts/live_v6_verify.py  # second pass")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
