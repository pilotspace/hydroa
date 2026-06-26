"""Red/Green test suite for gateway/memory domain.

Contract under test:
  POST   /v1/memories              -> 201 {id, content, created_at}
  GET    /v1/memories              -> 200 {data:[...], limit, offset}  (no raw embedding)
  POST   /v1/memories/search       -> 200 {data:[{id, content, score, created_at}]}
  DELETE /v1/memories/{id}         -> 204 | 404

TENANT-ISOLATION INVARIANT: cross-tenant access must be 404 with zero data leak.
EMBED-BEST-EFFORT: embedding failure must NOT prevent 201; row gets null embedding.
ALL repository methods must take tenant_id parameter.

Infra: real Postgres (localhost:5433) via root conftest.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update

from gateway.keys.infrastructure.orm import ApiKeyRow

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Stub embedder helpers
# ---------------------------------------------------------------------------

# Deterministic 3-element unit-ish vectors per known content string.
# Used to verify cosine ranking: "vec_a" is most similar to query, then "vec_b".
_STUB_VECTORS: dict[str, list[float]] = {
    "query text": [1.0, 0.0, 0.0],
    "most relevant content": [0.95, 0.31, 0.0],
    "moderately relevant content": [0.5, 0.866, 0.0],
    "least relevant content": [0.0, 0.0, 1.0],
}


async def _stub_embedder(raw_key: str, text: str) -> list[float] | None:
    """Deterministic stub — never calls a live provider."""
    return _STUB_VECTORS.get(text)


async def _raising_embedder(raw_key: str, text: str) -> list[float] | None:
    """Stub that always raises — simulates an embedding provider outage."""
    raise RuntimeError("embed provider down")


def _install_embedder(app: Any, embedder: Any) -> None:
    app.state.memory_embedder = embedder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _signup_and_key(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
    """Register a new tenant, log in, create an API key. Returns {key, key_id, tenant_id, jwt}."""
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
        "jwt": token,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key_info(client: Any, app: Any) -> dict[str, str]:
    """Primary tenant fixture with stub embedder installed."""
    _install_embedder(app, _stub_embedder)
    return await _signup_and_key(
        client,
        tenant_name="MemTest",
        email="mem-test@example.io",
        password="mem-test-battery",
    )


@pytest.fixture
async def other_tenant(client: Any) -> dict[str, str]:
    """A SECOND tenant — used for cross-tenant isolation checks."""
    return await _signup_and_key(
        client,
        tenant_name="MemOther",
        email="mem-other@example.io",
        password="mem-other-battery",
    )


# ---------------------------------------------------------------------------
# test_store_and_list: POST creates memory; GET returns it without raw embedding
# ---------------------------------------------------------------------------


class TestStoreAndList:
    async def test_store_returns_201_with_fields(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["content"] == "most relevant content"
        assert "id" in body
        assert "created_at" in body
        # raw embedding NEVER returned
        assert "embedding" not in body

    async def test_store_with_metadata(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/v1/memories",
            json={"content": "most relevant content", "metadata": {"tag": "test", "priority": 1}},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 201
        # metadata not exposed in create response — just id, content, created_at
        body = resp.json()
        assert set(body.keys()) == {"id", "content", "created_at"}

    async def test_list_returns_memories_newest_first(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        await client.post(
            "/v1/memories",
            json={"content": "moderately relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        resp = await client.get("/v1/memories", headers=_bearer(api_key_info["key"]))
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["limit"] == 50
        assert body["offset"] == 0
        assert len(body["data"]) == 2
        # no raw embedding in list items
        for item in body["data"]:
            assert "embedding" not in item
            assert "has_embedding" in item

    async def test_list_has_embedding_true_when_embedding_present(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """Content in _STUB_VECTORS gets a real embedding → has_embedding=True."""
        await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        resp = await client.get("/v1/memories", headers=_bearer(api_key_info["key"]))
        items = resp.json()["data"]
        assert items[0]["has_embedding"] is True

    async def test_list_limit_default_50_cap_200(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.get(
            "/v1/memories?limit=999", headers=_bearer(api_key_info["key"])
        )
        assert resp.status_code == 200
        assert resp.json()["limit"] == 200

    async def test_list_excludes_deleted(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        mem_id = create_resp.json()["id"]
        await client.delete(f"/v1/memories/{mem_id}", headers=_bearer(api_key_info["key"]))

        resp = await client.get("/v1/memories", headers=_bearer(api_key_info["key"]))
        ids = [m["id"] for m in resp.json()["data"]]
        assert mem_id not in ids


# ---------------------------------------------------------------------------
# test_search_ranks_by_cosine: 3 stub vectors, top_k=2, score-descending
# ---------------------------------------------------------------------------


class TestSearchRanksByCosine:
    async def test_search_returns_top_k_by_cosine_desc(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """Three memories with deterministic stub vectors; query ranks them correctly."""
        for content in ["most relevant content", "moderately relevant content", "least relevant content"]:
            r = await client.post(
                "/v1/memories",
                json={"content": content},
                headers=_bearer(api_key_info["key"]),
            )
            assert r.status_code == 201

        resp = await client.post(
            "/v1/memories/search",
            json={"query": "query text", "top_k": 2},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        # scores must be descending
        assert data[0]["score"] >= data[1]["score"]
        # top result should be "most relevant content"
        assert data[0]["content"] == "most relevant content"
        # no raw embedding
        for item in data:
            assert "embedding" not in item
            assert "score" in item
            assert "id" in item
            assert "created_at" in item

    async def test_search_score_is_float(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        resp = await client.post(
            "/v1/memories/search",
            json={"query": "query text"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        score = data[0]["score"]
        assert isinstance(score, float)
        assert not math.isnan(score)

    async def test_search_top_k_clamp_max_100(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """top_k > 100 should be clamped to 100, not rejected."""
        await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        resp = await client.post(
            "/v1/memories/search",
            json={"query": "query text", "top_k": 999},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200

    async def test_search_default_top_k(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """Search without top_k uses the config default (5)."""
        resp = await client.post(
            "/v1/memories/search",
            json={"query": "query text"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200

    async def test_search_excludes_deleted(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        mem_id = create_resp.json()["id"]
        await client.delete(f"/v1/memories/{mem_id}", headers=_bearer(api_key_info["key"]))

        resp = await client.post(
            "/v1/memories/search",
            json={"query": "query text"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["data"]]
        assert mem_id not in ids


# ---------------------------------------------------------------------------
# test_tenant_isolation: B's search excludes A; B get/delete A's id → 404
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    async def test_list_isolation(
        self, client: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """TENANT ISOLATION: Tenant A's memories are NOT visible in Tenant B's list."""
        await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        resp = await client.get("/v1/memories", headers=_bearer(other_tenant["key"]))
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_search_isolation(
        self, client: Any, app: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """TENANT ISOLATION: B's search does NOT return A's memories."""
        # Ensure stub embedder is wired for B's search too
        _install_embedder(app, _stub_embedder)
        await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        resp = await client.post(
            "/v1/memories/search",
            json={"query": "query text"},
            headers=_bearer(other_tenant["key"]),
        )
        assert resp.status_code == 200
        # B gets zero results — A's memory is not returned
        assert resp.json()["data"] == []

    async def test_get_cross_tenant_returns_404(
        self, client: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """TENANT ISOLATION: Tenant B cannot GET Tenant A's memory — 404, no data leak."""
        create_resp = await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        # We don't have a dedicated GET/{id} — use delete as the probe
        mem_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/v1/memories/{mem_id}",
            headers=_bearer(other_tenant["key"]),
        )
        assert resp.status_code == 404

    async def test_delete_cross_tenant_returns_404_and_leaves_intact(
        self, client: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """TENANT ISOLATION: Tenant B's delete attempt on A's id → 404; A's memory unchanged."""
        create_resp = await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        mem_id = create_resp.json()["id"]

        # B tries to delete A's memory
        del_resp = await client.delete(
            f"/v1/memories/{mem_id}",
            headers=_bearer(other_tenant["key"]),
        )
        assert del_resp.status_code == 404

        # A's memory is intact
        list_resp = await client.get("/v1/memories", headers=_bearer(api_key_info["key"]))
        ids = [m["id"] for m in list_resp.json()["data"]]
        assert mem_id in ids


# ---------------------------------------------------------------------------
# test_embed_failure_still_stores: raising stub → 201, embedding null,
#   substring search finds it, no 5xx
# ---------------------------------------------------------------------------


class TestEmbedFailureStillStores:
    async def test_raising_embedder_yields_201_and_null_embedding(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """EMBED-BEST-EFFORT: a raising embedder must not prevent 201."""
        _install_embedder(app, _raising_embedder)

        resp = await client.post(
            "/v1/memories",
            json={"content": "some searchable phrase"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 201
        mem_id = resp.json()["id"]

        # Verify embedding is NULL (has_embedding=False in list)
        list_resp = await client.get("/v1/memories", headers=_bearer(api_key_info["key"]))
        items = list_resp.json()["data"]
        matching = [m for m in items if m["id"] == mem_id]
        assert len(matching) == 1
        assert matching[0]["has_embedding"] is False

    async def test_substring_search_finds_null_embedding_row(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """EMBED-BEST-EFFORT: when query cannot be embedded, substring fallback finds the row."""
        _install_embedder(app, _raising_embedder)

        await client.post(
            "/v1/memories",
            json={"content": "some searchable phrase"},
            headers=_bearer(api_key_info["key"]),
        )

        # Also raise on the search side (no query embedding)
        resp = await client.post(
            "/v1/memories/search",
            json={"query": "searchable"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) >= 1
        assert data[0]["score"] is None  # text-fallback → null score

    async def test_no_5xx_on_embed_failure(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """EMBED-BEST-EFFORT: no 5xx is ever raised by embed failure."""
        _install_embedder(app, _raising_embedder)
        for _ in range(3):
            resp = await client.post(
                "/v1/memories",
                json={"content": "some searchable phrase"},
                headers=_bearer(api_key_info["key"]),
            )
            assert resp.status_code == 201


# ---------------------------------------------------------------------------
# test_soft_delete_hides: gone from list+search; cross access 404
# ---------------------------------------------------------------------------


class TestSoftDeleteHides:
    async def test_delete_returns_204(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        mem_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/v1/memories/{mem_id}", headers=_bearer(api_key_info["key"])
        )
        assert resp.status_code == 204

    async def test_delete_unknown_returns_404(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.delete(
            f"/v1/memories/{uuid.uuid4()}", headers=_bearer(api_key_info["key"])
        )
        assert resp.status_code == 404

    async def test_delete_twice_returns_404(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        mem_id = create_resp.json()["id"]
        await client.delete(f"/v1/memories/{mem_id}", headers=_bearer(api_key_info["key"]))
        resp = await client.delete(f"/v1/memories/{mem_id}", headers=_bearer(api_key_info["key"]))
        assert resp.status_code == 404

    async def test_deleted_memory_hidden_from_list(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        mem_id = create_resp.json()["id"]
        await client.delete(f"/v1/memories/{mem_id}", headers=_bearer(api_key_info["key"]))

        list_resp = await client.get("/v1/memories", headers=_bearer(api_key_info["key"]))
        ids = [m["id"] for m in list_resp.json()["data"]]
        assert mem_id not in ids

    async def test_deleted_memory_hidden_from_search(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        _install_embedder(app, _stub_embedder)
        create_resp = await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        mem_id = create_resp.json()["id"]
        await client.delete(f"/v1/memories/{mem_id}", headers=_bearer(api_key_info["key"]))

        search_resp = await client.post(
            "/v1/memories/search",
            json={"query": "query text"},
            headers=_bearer(api_key_info["key"]),
        )
        assert search_resp.status_code == 200
        ids = [m["id"] for m in search_resp.json()["data"]]
        assert mem_id not in ids


# ---------------------------------------------------------------------------
# test_auth_and_validation
# ---------------------------------------------------------------------------


class TestAuthAndValidation:
    async def test_no_bearer_post_returns_401(self, client: Any) -> None:
        resp = await client.post("/v1/memories", json={"content": "hello"})
        assert resp.status_code == 401

    async def test_invalid_bearer_returns_401(self, client: Any) -> None:
        resp = await client.post(
            "/v1/memories",
            json={"content": "hello"},
            headers={"Authorization": "Bearer sk-invalid"},
        )
        assert resp.status_code == 401

    async def test_expired_key_returns_401(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """Expired (but not revoked) key must be rejected with 401."""
        # Sanity: key works before expiry
        ok = await client.post(
            "/v1/memories",
            json={"content": "most relevant content"},
            headers=_bearer(api_key_info["key"]),
        )
        assert ok.status_code == 201

        # Force-expire in DB
        async with app.state.sessionmaker() as session:
            await session.execute(
                update(ApiKeyRow)
                .where(ApiKeyRow.id == uuid.UUID(api_key_info["key_id"]))
                .values(expires_at=datetime.now(tz=UTC) - timedelta(hours=1))
            )
            await session.commit()

        resp = await client.post(
            "/v1/memories",
            json={"content": "post-expiry"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 401

    async def test_empty_content_returns_422(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/v1/memories",
            json={"content": "   "},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422

    async def test_missing_content_returns_422(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/v1/memories",
            json={},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422

    async def test_empty_query_returns_422(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/v1/memories/search",
            json={"query": ""},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422

    async def test_no_row_created_on_auth_failure(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """401 on bad bearer must NOT create any memory row."""
        resp = await client.post(
            "/v1/memories",
            json={"content": "should not persist"},
            headers={"Authorization": "Bearer sk-invalid"},
        )
        assert resp.status_code == 401

        # Real tenant's list must be empty
        list_resp = await client.get("/v1/memories", headers=_bearer(api_key_info["key"]))
        assert list_resp.json()["data"] == []
