#!/usr/bin/env python3
"""Live OpenRouter smoke — contract: .add/tasks/live-upstream-smoke/TASK.md §3.

Operator-run (requires a real key + the e2e TLS stack):
    export GATEWAY_OPENROUTER_API_KEY=sk-or-...
    docker compose -f infra/docker-compose.e2e.yml up --build -d --wait
    cd apps/gateway && uv run python ../../scripts/live_smoke.py

Exit codes: 0 = reconciled · 1 = reconciliation/flow failure · 2 = key absent.
The key is read from the environment and never printed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from decimal import Decimal

MODEL = os.environ.get("SMOKE_MODEL", "nvidia/nemotron-3-nano-30b-a3b:free")
BASE = os.environ.get("SMOKE_BASE", "https://localhost:8443")
CA = os.environ.get("E2E_CA_CERT", "infra/envoy/certs/dev-ca.pem")
LEDGER_POLL_SECONDS = 15


def fail(msg: str) -> None:
    print(f"SMOKE FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if not os.environ.get("GATEWAY_OPENROUTER_API_KEY", "").strip():
        print(
            "SMOKE REFUSED: GATEWAY_OPENROUTER_API_KEY is unset/empty. "
            "Export a real OpenRouter key and bring up infra/docker-compose.e2e.yml "
            "with it before running.",
            file=sys.stderr,
        )
        sys.exit(2)

    import httpx  # deferred: not needed for the no-key refusal path

    ca = CA if os.path.exists(CA) else os.path.join("..", "..", CA)
    client = httpx.Client(verify=ca, timeout=90)
    email = f"smoke-{int(time.time())}@live.io"
    password = "live-smoke-password-1"

    # Tenant + key, all through the Envoy TLS edge.
    r = client.post(
        f"{BASE}/admin/auth/signup",
        json={"tenant_name": "LiveSmokeCo", "email": email, "password": password},
    )
    if r.status_code != 201:
        fail(f"signup failed: {r.status_code} {r.text[:300]}")
    jwt = client.post(
        f"{BASE}/admin/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    k = client.post(
        f"{BASE}/admin/keys",
        json={"name": "live-smoke"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    if k.status_code != 201:
        fail(f"key creation failed: {k.status_code} {k.text[:300]}")
    api_key = k.json()["key"]

    # Catalog sync runs inside the gateway container (/internal is edge-blocked).
    sync = subprocess.run(
        [
            "docker", "exec", "hydroa-e2e-gateway-1", "python", "-c",
            "import urllib.request,json;"
            "req=urllib.request.Request('http://localhost:8000/internal/catalog/sync',method='POST');"
            "print(json.load(urllib.request.urlopen(req,timeout=60))['synced'])",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if sync.returncode != 0:
        fail(f"catalog sync failed: {sync.stderr[:300]}")
    print(f"catalog synced: {sync.stdout.strip()} models")

    # Live streamed completion.
    upstream_usage: dict | None = None
    got_done = False
    with client.stream(
        "POST",
        f"{BASE}/v1/chat/completions",
        json={
            "model": MODEL,
            "stream": True,
            "messages": [{"role": "user", "content": "Reply with exactly: SMOKE OK"}],
        },
        headers={"Authorization": f"Bearer {api_key}"},
    ) as resp:
        if resp.status_code != 200:
            fail(f"completion status {resp.status_code}: {resp.read()[:300]!r}")
        for line in resp.iter_lines():
            if line == "data: [DONE]":
                got_done = True
            elif line.startswith("data: "):
                try:
                    frame = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if isinstance(frame.get("usage"), dict):
                    upstream_usage = frame["usage"]
    if not got_done:
        fail("stream ended without [DONE]")
    if upstream_usage is None:
        fail("upstream stream carried no usage frame")
    up_pt = int(upstream_usage["prompt_tokens"])
    up_ct = int(upstream_usage["completion_tokens"])
    print(f"upstream usage: prompt={up_pt} completion={up_ct}")

    # Ledger reconciliation (poll: write-behind flusher).
    record = None
    deadline = time.time() + LEDGER_POLL_SECONDS
    while time.time() < deadline:
        u = client.get(
            f"{BASE}/admin/usage", headers={"Authorization": f"Bearer {jwt}"}
        ).json()
        records = u["records"] if isinstance(u, dict) else u
        if records:
            record = records[0]
            break
        time.sleep(1)
    if record is None:
        fail(f"no ledger row within {LEDGER_POLL_SECONDS}s")

    led_pt, led_ct = int(record["prompt_tokens"]), int(record["completion_tokens"])
    if (led_pt, led_ct) != (up_pt, up_ct):
        fail(
            f"token divergence — ledger ({led_pt}/{led_ct}) != upstream ({up_pt}/{up_ct})"
        )

    # Cost reconciliation against the persisted pricing snapshot + markup.
    pricing = subprocess.run(
        [
            "docker", "exec", "hydroa-e2e-postgres-1", "psql", "-U", "gateway",
            "-d", "gateway_e2e", "-At", "-c",
            "SELECT p.prompt_usd_per_token, p.completion_usd_per_token, t.markup_pct "
            "FROM pricing_snapshots p, tenants t "
            f"WHERE p.model_id = '{MODEL}' AND t.name = 'LiveSmokeCo' "
            "ORDER BY p.captured_at DESC LIMIT 1;",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    prompt_price, completion_price, markup = (
        Decimal(x) for x in pricing.stdout.strip().split("|")
    )
    expected = (up_pt * prompt_price + up_ct * completion_price) * (
        1 + markup / Decimal(100)
    )
    actual = Decimal(record["cost_usd"])
    if actual != expected.quantize(actual):
        fail(f"cost divergence — ledger {actual} != expected {expected}")

    print(
        f"SMOKE OK: model={MODEL} tokens={up_pt}/{up_ct} "
        f"cost_usd={actual} (reconciled, markup={markup}%)"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
