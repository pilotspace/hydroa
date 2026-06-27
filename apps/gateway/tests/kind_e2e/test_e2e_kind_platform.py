"""LIVE kind-cluster platform-features e2e — v53 task 8 (e2e-platform-features §1–§3).

Extends the core-flow e2e to prove THREE platform surfaces through the LIVE Envoy edge:
  A · realtime-relay WS handshake honest-degrades (4404) — WS-upgrade + TLS + auth-over-WS
  B · an artifact round-trips through MinIO (storage_backend='s3', not inline BYTEA)
  C · key admin reads answer behind the edge (jwt_authn)

@pytest.mark.kind_e2e → excluded from the default suite; run via `make kind-e2e`.
M2 is RED until values-kind ENABLES object store (then the row is 's3', not 'inline').

Coverage:
  test_relay_honest_degrades_over_ws            M1
  test_artifact_round_trips_through_minio       M2
  test_admin_reads_answer_behind_edge           M3
  test_relay_rejects_bad_token                  R1
  test_unauthenticated_artifact_rejected_at_edge R2
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.kind_e2e.conftest import (
    ART_BYTES,
    ART_CONTENT_TYPE,
    ARTIFACTS_PATH,
    EDGE_URL,
    RELAY_CLOSE_BAD_AUTH,
    RELAY_CLOSE_NO_PROVIDER,
    admin_get,
    create_artifact,
    create_key,
    get_artifact,
    read_artifact_storage,
    read_tenant_id,
    signup_and_login,
    unique_suffix,
    ws_relay_close_code,
)

pytestmark = pytest.mark.kind_e2e


async def test_relay_honest_degrades_over_ws(edge_client: httpx.AsyncClient) -> None:
    """M1: a VALID key over the relay WS → server closes 4404 (no provider — honest-degrade).

    Proves Envoy upgraded the WS + terminated TLS + the gateway ran auth-over-WS, then degraded
    honestly because kind configures no realtime provider. (edge_client unused — the WS client
    has its own connection — but the fixture guarantees the stack is reachable.)
    """
    sfx = unique_suffix()
    jwt = await signup_and_login(edge_client, tenant=f"KindWS-{sfx}", email=f"ws-{sfx}@kind.e2e")
    key_body = await create_key(edge_client, jwt, f"kind-ws-{sfx}")

    code = await ws_relay_close_code(key_body["key"])
    assert code == RELAY_CLOSE_NO_PROVIDER, (
        f"relay closed with {code}, expected {RELAY_CLOSE_NO_PROVIDER} (valid key, no provider). "
        "A 4401 here would mean auth-over-WS rejected a valid key; 1006 would mean the edge "
        "dropped the connection instead of passing the app close code."
    )


async def test_artifact_round_trips_through_minio(edge_client: httpx.AsyncClient) -> None:
    """M2: POST→GET an artifact returns the exact bytes AND the row proves it landed in MinIO.

    RED until values-kind enables object store: with object store off the row is
    storage_backend='inline' (Postgres BYTEA), so the 's3' assertion fails.
    """
    sfx = unique_suffix()
    jwt = await signup_and_login(edge_client, tenant=f"KindArt-{sfx}", email=f"art-{sfx}@kind.e2e")
    key_body = await create_key(edge_client, jwt, f"kind-art-{sfx}")
    api_key = key_body["key"]

    created = await create_artifact(
        edge_client, api_key, name=f"e2e-{sfx}.txt", content_type=ART_CONTENT_TYPE, data=ART_BYTES
    )
    assert created["size_bytes"] == len(ART_BYTES), f"size mismatch: {created}"

    # Round-trip: the bytes come back EXACTLY, with the stored content-type.
    resp = await get_artifact(edge_client, api_key, created["id"])
    assert resp.status_code == 200, (
        f"artifact GET not 200 ({resp.status_code}): {resp.text[:300]!r}"
    )
    assert resp.content == ART_BYTES, "artifact bytes did not round-trip exactly"
    assert resp.headers.get("content-type", "").startswith(ART_CONTENT_TYPE)

    # MinIO proof: the row must be object-store-backed, not inline.
    backend, object_key = read_artifact_storage(created["id"])
    assert backend == "s3", (
        f"artifact stored as {backend!r}, expected 's3' — object store is NOT enabled in kind "
        "(bytes went to inline Postgres BYTEA, not MinIO)."
    )
    tenant_id = read_tenant_id(str(key_body["key_id"]))
    assert object_key == f"artifacts/{tenant_id}/{created['id']}", (
        f"unexpected object_key {object_key!r} for tenant {tenant_id} / artifact {created['id']}"
    )


async def test_admin_reads_answer_behind_edge(edge_client: httpx.AsyncClient) -> None:
    """M3: a representative set of /admin reads return 200 behind the edge (jwt_authn)."""
    sfx = unique_suffix()
    jwt = await signup_and_login(edge_client, tenant=f"KindAdm-{sfx}", email=f"adm-{sfx}@kind.e2e")
    key_body = await create_key(edge_client, jwt, f"kind-adm-{sfx}")

    me = await admin_get(edge_client, jwt, "/admin/auth/me")
    assert me.status_code == 200, f"/admin/auth/me not 200 ({me.status_code}): {me.text[:300]!r}"

    keys = await admin_get(edge_client, jwt, "/admin/keys")
    assert keys.status_code == 200, f"/admin/keys not 200 ({keys.status_code}): {keys.text[:300]!r}"
    assert str(key_body["key_id"]) in keys.text, "the created key_id is not listed in /admin/keys"

    usage = await admin_get(edge_client, jwt, "/admin/usage")
    assert usage.status_code == 200, (
        f"/admin/usage not 200 ({usage.status_code}): {usage.text[:300]!r}"
    )
    assert "records" in usage.json(), f"/admin/usage missing records: {usage.text[:300]!r}"


async def test_relay_rejects_bad_token(edge_client: httpx.AsyncClient) -> None:
    """R1: an INVALID token over the relay WS → close 4401 (NOT 4404), no provider session."""
    code = await ws_relay_close_code("not-a-real-key")
    assert code == RELAY_CLOSE_BAD_AUTH, (
        f"relay closed with {code}, expected {RELAY_CLOSE_BAD_AUTH} for a bad token — "
        "auth-over-WS must reject before the provider step (4404 would mean it didn't validate)."
    )


async def test_unauthenticated_artifact_rejected_at_edge(edge_client: httpx.AsyncClient) -> None:
    """R2: GET /v1/artifacts/{id} with no API key → rejected AT the edge (ext_authz on /v1/*)."""
    resp = await edge_client.get(f"{EDGE_URL}{ARTIFACTS_PATH}/{uuid.uuid4()}")
    assert resp.status_code in (401, 403), (
        f"unauthenticated artifact GET was NOT rejected at the edge ({resp.status_code}): "
        f"{resp.text[:300]!r}"
    )
