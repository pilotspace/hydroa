"""LIVE kind-cluster core-flow e2e — v53 task 7 (e2e-core-flow §1–§3).

Drives the full money path through the LIVE kind Envoy edge and proves the platform
records an ACCURATE, NON-ZERO usage+cost row — the milestone's exit criterion.

@pytest.mark.kind_e2e → excluded from the default suite; run via `make kind-e2e`
(which brings the stack up AND seeds pricing for E2E_MODEL). Without the seed the
accurate-cost assertion (M3) is RED by design: an unpriced model bills $0.

Coverage:
  test_priced_completion_records_accurate_cost   M1 + M2 + M3
  test_keyed_completion_accepted_at_edge         M1 (ext_authz passes a valid key over TLS)
  test_unauthenticated_v1_rejected_at_edge       R1
  test_unpriced_model_bills_zero                 R2 (honest-degrade contrast)
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from tests.kind_e2e.conftest import (
    COMPLETION_PRICE,
    E2E_MODEL,
    EDGE_URL,
    PROMPT_PRICE,
    STUB_COMPLETION_TOKENS,
    STUB_PROMPT_TOKENS,
    UNPRICED_MODEL,
    complete,
    create_key,
    poll_usage_record,
    read_tenant_markup_pct,
    register_provider_key,
    signup_and_login,
    unique_suffix,
)

pytestmark = pytest.mark.kind_e2e


async def test_priced_completion_records_accurate_cost(edge_client: httpx.AsyncClient) -> None:
    """M1+M2+M3: signup→login→key→priced completion → exact non-zero cost row.

    Requires scripts/e2e_kind.sh to have seeded pricing for E2E_MODEL (P, C).
    """
    sfx = unique_suffix()
    jwt = await signup_and_login(edge_client, tenant=f"KindE2E-{sfx}", email=f"core-{sfx}@kind.e2e")
    # M5 (v2) — BYOK precondition: register a tenant provider key before completing.
    await register_provider_key(edge_client, jwt)
    key_body = await create_key(edge_client, jwt, f"kind-e2e-{sfx}")
    api_key = key_body["key"]

    # M1 — proxied completion through the edge returns the stub usage.
    resp = await complete(edge_client, api_key, E2E_MODEL)
    assert resp.status_code == 200, f"completion not 200 ({resp.status_code}): {resp.text[:400]!r}"
    usage = resp.json().get("usage", {})
    assert usage.get("prompt_tokens") == STUB_PROMPT_TOKENS
    assert usage.get("completion_tokens") == STUB_COMPLETION_TOKENS

    # M3 — the usage row eventually lands with the EXACT, non-zero billed cost.
    record = await poll_usage_record(edge_client, jwt, E2E_MODEL)
    assert record is not None, (
        f"no usage row for {E2E_MODEL} within the poll window — is the in-cluster "
        "flusher running, and was pricing seeded?"
    )
    assert record["prompt_tokens"] == STUB_PROMPT_TOKENS
    assert record["completion_tokens"] == STUB_COMPLETION_TOKENS
    assert record["status"] == 200

    markup_pct = read_tenant_markup_pct(str(key_body["key_id"]))
    expected = (
        Decimal(STUB_PROMPT_TOKENS) * PROMPT_PRICE
        + Decimal(STUB_COMPLETION_TOKENS) * COMPLETION_PRICE
    ) * (Decimal(1) + markup_pct / Decimal(100))
    actual = Decimal(record["cost_usd"])
    assert actual > 0, f"cost_usd is $0 — pricing was NOT seeded for {E2E_MODEL}"
    assert actual == expected, (
        f"inaccurate cost: got {actual}, expected {expected} "
        f"= (9*{PROMPT_PRICE} + 7*{COMPLETION_PRICE}) * (1 + {markup_pct}/100)"
    )


async def test_keyed_completion_accepted_at_edge(edge_client: httpx.AsyncClient) -> None:
    """M1: a VALID API key is accepted by Envoy ext_authz over TLS (not an edge 401/403)."""
    sfx = unique_suffix()
    jwt = await signup_and_login(
        edge_client, tenant=f"KindEdge-{sfx}", email=f"edge-{sfx}@kind.e2e"
    )
    await register_provider_key(edge_client, jwt)  # M5 (v2) BYOK precondition
    key_body = await create_key(edge_client, jwt, f"kind-edge-{sfx}")
    resp = await complete(edge_client, key_body["key"], E2E_MODEL)
    assert resp.status_code not in (401, 403), (
        f"the edge rejected a VALID key before the gateway ({resp.status_code}): "
        f"{resp.text[:400]!r} — ext_authz / Bearer path is broken."
    )
    assert resp.status_code == 200


async def test_unauthenticated_v1_rejected_at_edge(edge_client: httpx.AsyncClient) -> None:
    """R1: /v1 with no API key is rejected AT the edge (ext_authz) — never reaches upstream."""
    resp = await edge_client.post(
        f"{EDGE_URL}/v1/chat/completions",
        json={"model": E2E_MODEL, "messages": [{"role": "user", "content": "ping"}]},
    )
    assert resp.status_code in (401, 403), (
        f"unauthenticated /v1 was NOT rejected at the edge ({resp.status_code}): {resp.text[:300]!r}"
    )


async def test_unpriced_model_bills_zero(edge_client: httpx.AsyncClient) -> None:
    """R2: a completion for an UNPRICED model records cost_usd == 0 (honest-degrade contrast).

    Proves M3's non-zero cost comes from the SEED, not from the pipeline defaulting non-zero.
    UNPRICED_MODEL is seeded as a catalog `models` row (so it routes + exists) but has NO
    pricing_snapshots row.
    """
    sfx = unique_suffix()
    jwt = await signup_and_login(
        edge_client, tenant=f"KindZero-{sfx}", email=f"zero-{sfx}@kind.e2e"
    )
    await register_provider_key(edge_client, jwt)  # M5 (v2) BYOK precondition
    key_body = await create_key(edge_client, jwt, f"kind-zero-{sfx}")
    resp = await complete(edge_client, key_body["key"], UNPRICED_MODEL)
    assert resp.status_code == 200, (
        f"unpriced completion not 200 ({resp.status_code}): {resp.text[:300]!r}"
    )

    record = await poll_usage_record(edge_client, jwt, UNPRICED_MODEL)
    assert record is not None, f"no usage row for {UNPRICED_MODEL} within the poll window"
    assert Decimal(record["cost_usd"]) == 0, (
        f"unpriced model billed non-zero ({record['cost_usd']}) — honest-degrade broken"
    )
