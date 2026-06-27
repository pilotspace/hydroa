"""Fixtures + helpers for the LIVE kind-cluster e2e (v53 task 7, e2e-core-flow §3).

These tests carry @pytest.mark.kind_e2e and are EXCLUDED from the default run
(apps/gateway/pyproject.toml addopts: -m 'not e2e and not kind_e2e'). They run ONLY
when explicitly selected against a Ready kind stack — `make kind-e2e`, which:
  1. ensures `make kind-up` (the task-6 harness) brought the stack up,
  2. SEEDS pricing for E2E_MODEL into the in-cluster Postgres (the §5 implementation),
  3. invokes `uv run pytest -m kind_e2e --no-cov`.

The SEED lives in scripts/e2e_kind.sh, NOT here — so a raw `pytest -m kind_e2e` with
no seed leaves the accurate-cost assertion (M3) RED for the right reason: an unpriced
model bills $0. The seed is what turns it green (TDD red→green for THIS task).

IMPORTANT (mirrors tests/edge): individual tests must NOT skip — they must FAIL when
the cluster/seed is absent. Skipping would silently pass the gate.

All HTTP goes through the kind Envoy NodePort (https://127.0.0.1:8443, self-signed →
verify=False) — never the gateway ClusterIP — so TLS + jwt_authn + ext_authz are exercised.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest

# ── Frozen contract constants (§3) ────────────────────────────────────────────
EDGE_URL = os.environ.get("KIND_EDGE_URL", "https://127.0.0.1:8443")
PG_POD = os.environ.get("KIND_PG_POD", "ai-proxy-postgres-0")
PG_USER = os.environ.get("KIND_PG_USER", "gateway")
PG_DB = os.environ.get("KIND_PG_DB", "gateway")

# Dedicated, obviously-test catalog ids (seeded by scripts/e2e_kind.sh).
E2E_MODEL = "e2e-kind-priced"
UNPRICED_MODEL = "e2e-kind-unpriced"
# Per-token prices the seed writes for E2E_MODEL — distinct so a prompt/completion
# swap is caught. MUST stay in lockstep with the INSERT in scripts/e2e_kind.sh.
PROMPT_PRICE = Decimal("0.000002")
COMPLETION_PRICE = Decimal("0.000010")
# The stub upstream (infra/kind/upstream-stub.yaml) always reports this usage.
STUB_PROMPT_TOKENS = 9
STUB_COMPLETION_TOKENS = 7

SIGNUP_PATH = "/admin/auth/signup"
LOGIN_PATH = "/admin/auth/login"
ADMIN_KEYS_PATH = "/admin/keys"
USAGE_PATH = "/admin/usage"
V1_PATH = "/v1/chat/completions"

# BYOK precondition (v2): the gateway is BYOK-only — every completion resolves a
# per-tenant provider credential, so the e2e tenant MUST register one before completing.
# The kind upstream stub ignores the secret's value, so a dummy is fine; what matters is
# that the credential STORE can encrypt+persist it (proves the enc-key chart wiring works).
BYOK_PROVIDER = "openrouter"  # the provider every kind model routes to (values-kind.yaml)
PROVIDER_KEYS_PATH = f"/admin/provider-keys/{BYOK_PROVIDER}"
DUMMY_PROVIDER_SECRET = "sk-kind-e2e-dummy-not-a-real-key"  # noqa: S105 — throwaway test value

# Bounded waits everywhere (design-for-failure) — the flusher is a 1 s background task.
USAGE_POLL_TIMEOUT_S = float(os.environ.get("KIND_E2E_POLL_TIMEOUT", "30"))
USAGE_POLL_INTERVAL_S = 1.0
KUBECTL_TIMEOUT_S = 20.0
HTTP_TIMEOUT_S = 15.0


# ── psql read helper (kubectl exec into the StatefulSet pod) ──────────────────
def psql_scalar(sql: str) -> str:
    """Run a one-shot read query in the in-cluster Postgres, return the scalar (-t -A).

    Uses local-socket trust inside the pod (no password). Bounded timeout; raises a
    clear error on failure so a missing cluster fails loudly, never silently skips.
    """
    cmd = [
        "kubectl",
        "exec",
        PG_POD,
        "--",
        "psql",
        "-U",
        PG_USER,
        "-d",
        PG_DB,
        "-t",
        "-A",
        "-c",
        sql,
    ]
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        cmd, capture_output=True, text=True, timeout=KUBECTL_TIMEOUT_S
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"psql read failed (is the kind stack up?): {' '.join(cmd)}\n"
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    return proc.stdout.strip()


def read_tenant_markup_pct(key_id: str) -> Decimal:
    """Read the markup_pct of the tenant that owns the given API key (M3, §3)."""
    # key_id is a UUID minted by our own create-key call — cast to ENFORCE that and
    # eliminate any injection surface before interpolating into the one-shot read.
    kid = uuid.UUID(str(key_id))
    sql = (
        "SELECT t.markup_pct FROM tenants t "  # noqa: S608 — kid is a validated UUID; local kind DB
        "JOIN api_keys k ON k.tenant_id = t.id "
        f"WHERE k.id = '{kid}'"
    )
    raw = psql_scalar(sql)
    assert raw, f"no markup_pct row for key {key_id} — did signup/key-create succeed?"
    return Decimal(raw)


# ── HTTP helpers through the edge (mirror tests/edge/test_e2e_edge.py shapes) ──
async def signup_and_login(client: httpx.AsyncClient, *, tenant: str, email: str) -> str:
    password = "kind-e2e-correct-horse-battery"
    sr = await client.post(
        f"{EDGE_URL}{SIGNUP_PATH}",
        json={"tenant_name": tenant, "email": email, "password": password},
    )
    assert sr.status_code == 201, (
        f"signup failed ({sr.status_code}): {sr.text}\n"
        "Is the kind stack Ready and Envoy routing /admin/* to the gateway?"
    )
    lr = await client.post(f"{EDGE_URL}{LOGIN_PATH}", json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed ({lr.status_code}): {lr.text}"
    return lr.json()["access_token"]


async def create_key(client: httpx.AsyncClient, jwt: str, name: str) -> dict[str, Any]:
    resp = await client.post(
        f"{EDGE_URL}{ADMIN_KEYS_PATH}",
        json={"name": name},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 201, f"key create failed ({resp.status_code}): {resp.text}"
    body: dict[str, Any] = resp.json()
    assert "key" in body and "key_id" in body, f"unexpected key response: {body}"
    return body


async def register_provider_key(client: httpx.AsyncClient, jwt: str) -> None:
    """Register a tenant BYOK credential (v2 precondition for any completion).

    PUT /admin/provider-keys/openrouter {secret} — owner-only, Bearer JWT. Bearer
    providers use the field `secret` (azure uses `api_key`). A 200 here PROVES the
    chart wired GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY: without it the store raises
    ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE and this PUT 500s.
    """
    resp = await client.put(
        f"{EDGE_URL}{PROVIDER_KEYS_PATH}",
        json={"secret": DUMMY_PROVIDER_SECRET},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200, (
        f"BYOK register failed ({resp.status_code}): {resp.text[:400]!r}\n"
        "A 500 here means the chart did NOT wire GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY "
        "(ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE)."
    )


async def complete(client: httpx.AsyncClient, api_key: str, model: str) -> httpx.Response:
    return await client.post(
        f"{EDGE_URL}{V1_PATH}",
        json={"model": model, "messages": [{"role": "user", "content": "ping"}]},
        headers={"Authorization": f"Bearer {api_key}"},
    )


async def poll_usage_record(
    client: httpx.AsyncClient, jwt: str, model_id: str
) -> dict[str, Any] | None:
    """Poll GET /admin/usage (bounded) until a record for model_id appears; else None.

    The recorder is write-behind (Redis Stream → 1 s flusher → usage_records), so the
    row lands a beat after the completion returns — hence the bounded poll, not a sleep.
    """
    deadline = asyncio.get_event_loop().time() + USAGE_POLL_TIMEOUT_S
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(
            f"{EDGE_URL}{USAGE_PATH}", headers={"Authorization": f"Bearer {jwt}"}
        )
        if resp.status_code == 200:
            for rec in resp.json().get("records", []):
                if rec.get("model_id") == model_id:
                    return rec
        await asyncio.sleep(USAGE_POLL_INTERVAL_S)
    return None


@pytest.fixture
async def edge_client() -> Any:
    """An httpx client pinned to the self-signed kind edge (verify off)."""
    async with httpx.AsyncClient(verify=False, timeout=HTTP_TIMEOUT_S) as client:  # noqa: S501
        yield client


def unique_suffix() -> str:
    return uuid.uuid4().hex[:8]
