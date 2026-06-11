#!/usr/bin/env python3
"""Live v3 exit-criteria verification — all through the Envoy TLS edge.

Operator-run (requires a real key + the e2e TLS stack with the alerts overlay):
    export GATEWAY_OPENROUTER_API_KEY=sk-or-...
    docker compose -f infra/docker-compose.e2e.yml -f infra/docker-compose.e2e.alerts.yml \
        up --build -d --wait
    cd apps/gateway && uv run python ../../scripts/live_v3_verify.py

Criteria covered (v3 MILESTONE.md):
  C1 hard per-key budget -> 402 ERR_BUDGET_EXCEEDED live (free model spends 0,
     so the cap is set to 0.00 — the >= comparator makes the very next request
     cross; the full Redis-counter read + 402 path is exercised live)
  C2 model allowlist 403 ERR_MODEL_NOT_ALLOWED + rotation invalidates the old
     secret within one request
  C3 RPM burst -> 429 with Retry-After while a sibling key is unaffected
  C4 soft-budget crossing persists an alert_event AND the webhook is delivered
     to a test sink without blocking the request
  C5 GET /admin/spend reconciles exactly with usage_records for the window
  C6 disabling a model for a tenant takes effect on the next request without
     gateway restart; breaker-open and drain-timeout event types are delivered
     by the live dispatcher (rows inserted via psql — emission paths are
     integration-tested; delivery is what this criterion exercises live)

The key is read from the environment and never printed.
Exit codes: 0 = all criteria PASS · 1 = failure · 2 = key absent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid as uuidlib
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

MODEL = os.environ.get("SMOKE_MODEL", "nvidia/nemotron-3-nano-30b-a3b:free")
BASE = os.environ.get("SMOKE_BASE", "https://localhost:8443")
CA = os.environ.get("E2E_CA_CERT", "infra/envoy/certs/dev-ca.pem")
SINK_PORT = 9909
PG_CONTAINER = "hydroa-e2e-postgres-1"

RESULTS: list[tuple[str, bool, str]] = []
SINK_EVENTS: list[dict] = []
_sink_lock = threading.Lock()


def record(criterion: str, ok: bool, note: str) -> None:
    RESULTS.append((criterion, ok, note))
    print(f"{'PASS' if ok else 'FAIL'}  {criterion}: {note}")


class _SinkHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"_raw": body.decode("utf-8", "replace")}
        with _sink_lock:
            SINK_EVENTS.append(payload)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # silence
        pass


def sink_event_types() -> list[str]:
    with _sink_lock:
        return [e.get("event_type", "?") for e in SINK_EVENTS]


def wait_for_sink(event_type: str, timeout: float = 30.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _sink_lock:
            for e in SINK_EVENTS:
                if e.get("event_type") == event_type:
                    return e
        time.sleep(0.5)
    return None


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", "gateway", "-d", "gateway_e2e",
         "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def main() -> None:
    if not os.environ.get("GATEWAY_OPENROUTER_API_KEY", "").strip():
        print("REFUSED: GATEWAY_OPENROUTER_API_KEY unset.", file=sys.stderr)
        sys.exit(2)

    import httpx

    server = HTTPServer(("0.0.0.0", SINK_PORT), _SinkHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"sink listening on :{SINK_PORT}")

    ca = CA if os.path.exists(CA) else os.path.join("..", "..", CA)
    client = httpx.Client(verify=ca, timeout=90)

    def completion(raw_key: str) -> httpx.Response:
        return client.post(
            f"{BASE}/v1/chat/completions",
            json={"model": MODEL, "messages": [{"role": "user", "content": "Say OK."}],
                  "max_tokens": 8},
            headers={"Authorization": f"Bearer {raw_key}"},
        )

    # ── Arrange: tenant + auth through the TLS edge ────────────────────────────
    email = f"v3-verify-{int(time.time())}@live.io"
    password = "v3-verify-password-1"
    r = client.post(f"{BASE}/admin/auth/signup",
                    json={"tenant_name": "V3VerifyCo", "email": email, "password": password})
    assert r.status_code == 201, f"signup: {r.status_code} {r.text[:200]}"
    tenant_id = r.json()["tenant_id"]
    jwt = client.post(f"{BASE}/admin/auth/login",
                      json={"email": email, "password": password}).json()["access_token"]
    auth = {"Authorization": f"Bearer {jwt}"}

    def create_key(name: str, **fields) -> dict:
        resp = client.post(f"{BASE}/admin/keys", json={"name": name, **fields}, headers=auth)
        assert resp.status_code == 201, f"create_key {name}: {resp.status_code} {resp.text[:200]}"
        return resp.json()

    # ── C2: allowlist 403 + rotation invalidation ──────────────────────────────
    k2 = create_key("c2-allowlist", model_allowlist=["anthropic/claude-opus-4"])
    resp = completion(k2["key"])
    ok = resp.status_code == 403 and resp.json().get("code") == "ERR_MODEL_NOT_ALLOWED"
    record("C2a allowlist 403", ok, f"{resp.status_code} {resp.json().get('code')}")

    k2b = create_key("c2-rotate")
    old_secret = k2b["key"]
    resp = completion(old_secret)
    record("C2b pre-rotate 200", resp.status_code == 200, f"{resp.status_code}")
    rot = client.post(f"{BASE}/admin/keys/{k2b['key_id']}/rotate", json={}, headers=auth)
    assert rot.status_code == 201, f"rotate: {rot.status_code} {rot.text[:200]}"
    new_secret = rot.json()["key"]
    resp_old = completion(old_secret)  # the very next request with the old secret
    resp_new = completion(new_secret)
    ok = resp_old.status_code == 401 and resp_new.status_code == 200
    record("C2c rotation invalidates old within one request", ok,
           f"old={resp_old.status_code} new={resp_new.status_code}")

    # ── C3: RPM burst 429 + Retry-After, sibling unaffected ────────────────────
    k3 = create_key("c3-rpm", rpm_limit=2)
    k3_sib = create_key("c3-sibling")
    statuses = []
    retry_after = None
    for _ in range(4):
        resp = completion(k3["key"])
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            break
    sib = completion(k3_sib["key"])
    ok = 429 in statuses and retry_after is not None and sib.status_code == 200
    record("C3 RPM burst 429 + Retry-After, sibling 200", ok,
           f"statuses={statuses} retry_after={retry_after} sibling={sib.status_code}")

    # ── C1: hard budget 402 ────────────────────────────────────────────────────
    k1 = create_key("c1-budget")
    resp = completion(k1["key"])
    record("C1a unbudgeted completion 200", resp.status_code == 200, f"{resp.status_code}")
    patch = client.patch(f"{BASE}/admin/keys/{k1['key_id']}",
                         json={"monthly_budget_usd": "0.00"}, headers=auth)
    assert patch.status_code == 200, f"patch budget: {patch.status_code} {patch.text[:200]}"
    resp = completion(k1["key"])
    ok = resp.status_code == 402 and resp.json().get("code") == "ERR_BUDGET_EXCEEDED"
    record("C1b budget cap -> 402 live", ok, f"{resp.status_code} {resp.json().get('code')}")

    # ── C4: soft-budget alert_event + webhook to sink, request NOT blocked ─────
    k4 = create_key("c4-soft", soft_budget_usd="0.00")
    resp = completion(k4["key"])
    record("C4a soft-budget request not blocked", resp.status_code == 200, f"{resp.status_code}")
    evt = wait_for_sink("soft_budget_exceeded", timeout=30)
    row = psql("SELECT count(*) FROM alert_events WHERE event_type='soft_budget_exceeded'"
               f" AND key_id='{k4['key_id']}'")
    ok = evt is not None and row == "1"
    record("C4b alert_event persisted + webhook delivered", ok,
           f"sink={'yes' if evt else 'no'} rows={row}")

    # ── C5: /admin/spend reconciles with usage_records ─────────────────────────
    deadline = time.time() + 20
    db_count, db_cost = "0", "0"
    while time.time() < deadline:
        out = psql("SELECT count(*) || '|' || COALESCE(SUM(cost_usd),0) FROM usage_records"
                   f" WHERE tenant_id='{tenant_id}'")
        db_count, db_cost = out.split("|")
        if int(db_count) >= 5:  # all successful completions above are flushed
            time.sleep(2)  # settle: catch any in-flight flush
            out = psql("SELECT count(*) || '|' || COALESCE(SUM(cost_usd),0) FROM usage_records"
                       f" WHERE tenant_id='{tenant_id}'")
            db_count, db_cost = out.split("|")
            break
        time.sleep(1)
    spend = client.get(f"{BASE}/admin/spend?window=month", headers=auth).json()
    api_requests = spend["totals"]["requests"]
    api_cost = Decimal(spend["totals"]["cost_usd"])
    ok = api_requests == int(db_count) and api_cost == Decimal(db_cost)
    record("C5 /admin/spend reconciles with usage_records", ok,
           f"api=({api_requests}, {api_cost}) db=({db_count}, {Decimal(db_cost)})")

    # ── C6: model disable next-request, no restart ─────────────────────────────
    k6 = create_key("c6-modelmgmt")
    resp = completion(k6["key"])
    record("C6a model enabled 200", resp.status_code == 200, f"{resp.status_code}")
    put = client.put(f"{BASE}/admin/models/{MODEL}", json={"enabled": False}, headers=auth)
    assert put.status_code == 200, f"disable: {put.status_code} {put.text[:200]}"
    resp = completion(k6["key"])  # the very next request
    ok = resp.status_code == 403 and resp.json().get("code") == "ERR_MODEL_DISABLED"
    record("C6b disable effective next request (no restart)", ok,
           f"{resp.status_code} {resp.json().get('code')}")
    put = client.put(f"{BASE}/admin/models/{MODEL}", json={"enabled": True}, headers=auth)
    resp = completion(k6["key"])
    record("C6c re-enable effective next request", resp.status_code == 200, f"{resp.status_code}")

    # ── C6d: breaker-open + drain-timeout delivered by the live dispatcher ─────
    for etype in ("circuit_breaker_open", "drain_timeout"):
        psql("INSERT INTO alert_events (id, tenant_id, key_id, event_type, payload,"
             " created_at, dedupe_key) VALUES"
             f" ('{uuidlib.uuid4()}', NULL, NULL, '{etype}', '{{}}'::jsonb, now(),"
             f" '{etype}:live-verify-{uuidlib.uuid4()}')")
    ok_breaker = wait_for_sink("circuit_breaker_open", timeout=20) is not None
    ok_drain = wait_for_sink("drain_timeout", timeout=20) is not None
    record("C6d breaker-open + drain-timeout webhooks delivered", ok_breaker and ok_drain,
           f"breaker={'yes' if ok_breaker else 'no'} drain={'yes' if ok_drain else 'no'}"
           " (rows inserted via psql; emission paths integration-tested)")

    # ── Summary ────────────────────────────────────────────────────────────────
    print(f"\nsink received event types: {sink_event_types()}")
    failed = [c for c, ok, _ in RESULTS if not ok]
    print(f"\n{'ALL CRITERIA PASS' if not failed else 'FAILURES: ' + ', '.join(failed)}"
          f"  ({len(RESULTS) - len(failed)}/{len(RESULTS)})")
    server.shutdown()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
