"""RED suite for vector-store-core (/v1/vector_stores CRUD + pgvector substrate).

Contract under test (vector-store-core PLAN.md §3, DRAFT):
  POST   /v1/vector_stores          {name?, metadata?} -> 200 vector store object
                                     { id:"vs_<32hex>", object:"vector_store", created_at,
                                       name, usage_bytes:0, status:"completed",
                                       file_counts:{in_progress,completed,failed,cancelled,total},
                                       metadata }
  GET    /v1/vector_stores          -> 200 { object:"list", data:[...], has_more }
  GET    /v1/vector_stores/{id}     -> 200 object; 404 unknown/cross-tenant/malformed
  DELETE /v1/vector_stores/{id}     -> 200 { id, object:"vector_store.deleted", deleted:true }
                                     HARD delete — rows gone (cascade wipes vsf + chunks)

Schema under test: vector_stores + vector_store_files + vector_store_chunks
  (chunks: embedding vector(1536) + HNSW cosine index — the pgvector substrate,
   Tin-locked 2026-07-24).

TENANT-ISOLATION INVARIANT: cross-tenant access is 404 with zero leak (never 403/200).
ANTI-ENUMERATION: absent, cross-tenant, and malformed ids are ONE uniform 404 code.
BILLING INVARIANT: vector-store CRUD has no provider cost -> writes ZERO usage_records.
ZDR COMPOSE: the store CONTAINER is metadata-only -> a ZDR tenant may create one; the
  payload gate (raise_if_zdr) sits at the wave-2 chunk-write choke point, not here.

RED until the vector_stores module + migration are wired. Every test targets a route or
table that does not yet exist, so the suite fails for the RIGHT reason (missing
implementation), not a broken harness. DO NOT edit to make pass — that is Build's job.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers / fixtures (mirror apps/gateway/tests/files_uploads_api)
# ---------------------------------------------------------------------------


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _signup_and_key(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post("/admin/auth/login", json={"email": email, "password": password})
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": f"ci-key-{tenant_name}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
    }


@pytest.fixture
async def tenant_a(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="VecA", email="vec-a@example.io", password="vec-a-battery"
    )


@pytest.fixture
async def tenant_b(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="VecB", email="vec-b@example.io", password="vec-b-battery"
    )


async def _create_store(client: Any, key: str, **body: Any) -> Any:
    return await client.post("/v1/vector_stores", json=body, headers=_bearer(key))


async def _set_tenant_zdr(app: Any, tenant_id: str) -> None:
    """Flip tenants.zdr_enabled=true (mirrors retention_zdr suite's _set_tenant_zdr)."""
    async with app.state.sessionmaker() as session:
        await session.execute(
            text("UPDATE tenants SET zdr_enabled = true WHERE id = :tid"),
            {"tid": tenant_id},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# M1 — create returns the OpenAI vector store object
# ---------------------------------------------------------------------------


async def test_create_vector_store_returns_object(client: Any, tenant_a: dict[str, str]) -> None:
    """covers: M1 — POST /v1/vector_stores returns the wire object with vs_ id + zero counts."""
    resp = await _create_store(client, tenant_a["key"], name="kb-main", metadata={"team": "ml"})
    assert resp.status_code == 200, f"create failed: {resp.status_code} {resp.text}"
    obj = resp.json()
    assert obj["object"] == "vector_store"
    assert obj["id"].startswith("vs_") and len(obj["id"]) == 3 + 32, obj["id"]
    assert obj["name"] == "kb-main"
    assert obj["metadata"] == {"team": "ml"}
    assert obj["status"] == "completed"
    assert obj["usage_bytes"] == 0
    assert isinstance(obj["created_at"], int)
    assert obj["file_counts"] == {
        "in_progress": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "total": 0,
    }


async def test_create_without_name_or_metadata(client: Any, tenant_a: dict[str, str]) -> None:
    """covers: M1 — both request fields are optional (OpenAI wire); name/metadata null-safe."""
    resp = await _create_store(client, tenant_a["key"])
    assert resp.status_code == 200, f"create failed: {resp.status_code} {resp.text}"
    obj = resp.json()
    assert obj["object"] == "vector_store"
    assert obj["name"] is None
    assert obj["metadata"] == {}


# ---------------------------------------------------------------------------
# R:vector_store_name_too_long / R:vector_store_metadata_invalid
# ---------------------------------------------------------------------------


async def test_create_rejects_name_too_long(client: Any, tenant_a: dict[str, str]) -> None:
    """covers: R:vector_store_name_too_long — >256 chars -> 422, and NO row persisted."""
    resp = await _create_store(client, tenant_a["key"], name="x" * 257)
    assert resp.status_code == 422, f"expected 422, got {resp.status_code} {resp.text}"
    assert resp.json()["error"]["code"] == "ERR_VECTOR_STORE_NAME_TOO_LONG"
    # side-effect assertion: nothing persisted for the tenant
    listed = await client.get("/v1/vector_stores", headers=_bearer(tenant_a["key"]))
    assert listed.status_code == 200
    assert listed.json()["data"] == []


@pytest.mark.parametrize(
    "bad_metadata",
    [
        {"k" * 65: "v"},  # key > 64 chars
        {"k": "v" * 513},  # value > 512 chars
        {f"k{i}": "v" for i in range(17)},  # > 16 pairs
        {"k": 7},  # non-string value
        "not-an-object",  # not a mapping at all
    ],
)
async def test_create_rejects_invalid_metadata(
    client: Any, tenant_a: dict[str, str], bad_metadata: Any
) -> None:
    """covers: R:vector_store_metadata_invalid — 422 + named code, nothing persisted."""
    resp = await _create_store(client, tenant_a["key"], metadata=bad_metadata)
    assert resp.status_code == 422, f"expected 422, got {resp.status_code} {resp.text}"
    assert resp.json()["error"]["code"] == "ERR_VECTOR_STORE_METADATA_INVALID"
    listed = await client.get("/v1/vector_stores", headers=_bearer(tenant_a["key"]))
    assert listed.status_code == 200
    assert listed.json()["data"] == []


# ---------------------------------------------------------------------------
# M2 — list (tenant-scoped, newest first, has_more)
# ---------------------------------------------------------------------------


async def test_list_returns_tenant_stores_newest_first(
    client: Any, tenant_a: dict[str, str]
) -> None:
    """covers: M2 — list envelope, newest first, limit honored + has_more flag."""
    ids = []
    for name in ("first", "second", "third"):
        r = await _create_store(client, tenant_a["key"], name=name)
        assert r.status_code == 200
        ids.append(r.json()["id"])
    listed = await client.get("/v1/vector_stores?limit=2", headers=_bearer(tenant_a["key"]))
    assert listed.status_code == 200
    env = listed.json()
    assert env["object"] == "list"
    assert [o["name"] for o in env["data"]] == ["third", "second"]
    assert env["has_more"] is True
    rest = await client.get(
        "/v1/vector_stores?limit=2&offset=2", headers=_bearer(tenant_a["key"])
    )
    assert [o["name"] for o in rest.json()["data"]] == ["first"]
    assert rest.json()["has_more"] is False


# ---------------------------------------------------------------------------
# M3 — get
# ---------------------------------------------------------------------------


async def test_get_returns_store(client: Any, tenant_a: dict[str, str]) -> None:
    """covers: M3 — GET /v1/vector_stores/{id} round-trips the created object."""
    created = await _create_store(client, tenant_a["key"], name="kb-get")
    assert created.status_code == 200
    vs_id = created.json()["id"]
    got = await client.get(f"/v1/vector_stores/{vs_id}", headers=_bearer(tenant_a["key"]))
    assert got.status_code == 200
    assert got.json()["id"] == vs_id
    assert got.json()["name"] == "kb-get"
    assert got.json()["object"] == "vector_store"


# ---------------------------------------------------------------------------
# M5 / R:vector_store_not_found — tenant isolation + anti-enumeration
# ---------------------------------------------------------------------------


async def test_cross_tenant_get_and_delete_404(
    client: Any, tenant_a: dict[str, str], tenant_b: dict[str, str]
) -> None:
    """covers: M5, R:vector_store_not_found — tenant B sees A's store as a uniform 404."""
    created = await _create_store(client, tenant_a["key"], name="kb-private")
    assert created.status_code == 200
    vs_id = created.json()["id"]

    got = await client.get(f"/v1/vector_stores/{vs_id}", headers=_bearer(tenant_b["key"]))
    assert got.status_code == 404
    assert got.json()["error"]["code"] == "ERR_VECTOR_STORE_NOT_FOUND"

    deleted = await client.delete(f"/v1/vector_stores/{vs_id}", headers=_bearer(tenant_b["key"]))
    assert deleted.status_code == 404
    assert deleted.json()["error"]["code"] == "ERR_VECTOR_STORE_NOT_FOUND"

    # …and the row is UNCHANGED for its owner (rejection leaves state intact)
    still = await client.get(f"/v1/vector_stores/{vs_id}", headers=_bearer(tenant_a["key"]))
    assert still.status_code == 200

    # B's list never contains A's store
    b_list = await client.get("/v1/vector_stores", headers=_bearer(tenant_b["key"]))
    assert b_list.json()["data"] == []


async def test_unknown_and_malformed_id_uniform_404(
    client: Any, tenant_a: dict[str, str]
) -> None:
    """covers: R:vector_store_not_found — absent vs malformed ids are indistinguishable."""
    for bad in ("vs_" + "0" * 32, "vs_zzz", "file-abc", "totally-bogus"):
        r = await client.get(f"/v1/vector_stores/{bad}", headers=_bearer(tenant_a["key"]))
        assert r.status_code == 404, f"{bad}: {r.status_code}"
        assert r.json()["error"]["code"] == "ERR_VECTOR_STORE_NOT_FOUND", bad


# ---------------------------------------------------------------------------
# M4 — delete (hard, cascading, verified past the session boundary)
# ---------------------------------------------------------------------------


async def test_delete_removes_store_and_rows(
    client: Any, app: Any, tenant_a: dict[str, str]
) -> None:
    """covers: M4 — delete envelope + the rows are GONE (re-fetch after commit)."""
    created = await _create_store(client, tenant_a["key"], name="kb-del")
    assert created.status_code == 200
    vs_id = created.json()["id"]

    deleted = await client.delete(f"/v1/vector_stores/{vs_id}", headers=_bearer(tenant_a["key"]))
    assert deleted.status_code == 200
    assert deleted.json() == {"id": vs_id, "object": "vector_store.deleted", "deleted": True}

    got = await client.get(f"/v1/vector_stores/{vs_id}", headers=_bearer(tenant_a["key"]))
    assert got.status_code == 404

    # HARD delete: the row is gone at the DB level, not soft-hidden (fresh session)
    async with app.state.sessionmaker() as session:
        count = (
            await session.execute(text("SELECT count(*) FROM vector_stores"))
        ).scalar_one()
    assert count == 0, "vector_stores row must be hard-deleted (payload-at-rest hygiene)"

    # idempotence-of-absence: a second delete is the same uniform 404
    again = await client.delete(f"/v1/vector_stores/{vs_id}", headers=_bearer(tenant_a["key"]))
    assert again.status_code == 404


# ---------------------------------------------------------------------------
# R:auth — bearer key required
# ---------------------------------------------------------------------------


async def test_auth_required_401(client: Any) -> None:
    """covers: R:auth_key_invalid — no/garbage bearer key -> 401 on every endpoint."""
    assert (await client.post("/v1/vector_stores", json={})).status_code == 401
    assert (await client.get("/v1/vector_stores")).status_code == 401
    assert (
        await client.get("/v1/vector_stores/vs_" + "0" * 32, headers=_bearer("sk-bogus"))
    ).status_code == 401


# ---------------------------------------------------------------------------
# M6 — the pgvector substrate (schema + HNSW index + extension)
# ---------------------------------------------------------------------------


async def test_pgvector_schema_and_hnsw_index(app: Any) -> None:
    """covers: M6 — the three tables exist; chunks.embedding is vector(1536); HNSW cosine index.

    Runs against the app fixture's create_all schema (ORM parity); the migrations suite
    separately proves alembic parity via EXPECTED_TABLES. Requires the pgvector extension
    on the test postgres (dev-compose image swap — §3 provisioning plan).
    """
    async with app.state.sessionmaker() as session:
        tables = {
            r[0]
            for r in (
                await session.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND "
                        "tablename IN ('vector_stores','vector_store_files','vector_store_chunks')"
                    )
                )
            ).fetchall()
        }
        assert tables == {"vector_stores", "vector_store_files", "vector_store_chunks"}, (
            f"missing vector-store tables: got {tables}"
        )

        ext = (
            await session.execute(text("SELECT count(*) FROM pg_extension WHERE extname='vector'"))
        ).scalar_one()
        assert ext == 1, "pgvector extension must be installed (CREATE EXTENSION vector)"

        coltype = (
            await session.execute(
                text(
                    "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                    "JOIN pg_class c ON c.oid = a.attrelid "
                    "WHERE c.relname='vector_store_chunks' AND a.attname='embedding'"
                )
            )
        ).scalar_one()
        assert coltype == "vector(1536)", f"embedding column is {coltype}"

        hnsw = (
            await session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE tablename='vector_store_chunks' "
                    "AND indexdef ILIKE '%hnsw%'"
                )
            )
        ).fetchall()
        assert hnsw, "vector_store_chunks needs an HNSW index"
        assert "vector_cosine_ops" in hnsw[0][0], hnsw[0][0]


# ---------------------------------------------------------------------------
# M7 — no billable op, no usage record
# ---------------------------------------------------------------------------


async def test_crud_writes_no_usage_records(
    client: Any, app: Any, tenant_a: dict[str, str]
) -> None:
    """covers: M7 — the whole CRUD surface has no provider cost -> zero usage_records rows."""
    created = await _create_store(client, tenant_a["key"], name="kb-billing")
    assert created.status_code == 200
    vs_id = created.json()["id"]
    assert (
        await client.get("/v1/vector_stores", headers=_bearer(tenant_a["key"]))
    ).status_code == 200
    assert (
        await client.get(f"/v1/vector_stores/{vs_id}", headers=_bearer(tenant_a["key"]))
    ).status_code == 200
    assert (
        await client.delete(f"/v1/vector_stores/{vs_id}", headers=_bearer(tenant_a["key"]))
    ).status_code == 200

    async with app.state.sessionmaker() as session:
        count = (await session.execute(text("SELECT count(*) FROM usage_records"))).scalar_one()
    assert count == 0, "vector-store CRUD must never write a usage record"


# ---------------------------------------------------------------------------
# M8 — ZDR compose: the container is metadata-only
# ---------------------------------------------------------------------------


async def test_zdr_tenant_can_create_store(
    client: Any, app: Any, tenant_a: dict[str, str]
) -> None:
    """covers: M8 — a ZDR tenant may create/list/delete a store (no payload lands here);
    the raise_if_zdr choke point is the wave-2 chunk write, pinned by contract."""
    await _set_tenant_zdr(app, tenant_a["tenant_id"])
    resp = await _create_store(client, tenant_a["key"], name="zdr-ok")
    assert resp.status_code == 200, f"ZDR tenant blocked from metadata-only create: {resp.text}"
    assert resp.json()["object"] == "vector_store"
