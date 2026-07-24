"""RED — top-k cosine search over vector_store_chunks, tenant + store scoped.

Contract under test (file-search-tool PLAN.md §3, DRAFT):
  A NEW read method on VectorStoreFileRepository (reusing the frozen wave-1/2 chunk
  schema + HNSW cosine index; ZERO DDL):

      async def search_chunks(
          self, *, tenant_id: uuid.UUID, store_id: uuid.UUID,
          query_embedding: list[float], top_k: int,
      ) -> list[...]   # ordered nearest-first by cosine distance; each item exposes .content

  INVARIANTS:
    - Ordered by cosine distance ASC (nearest chunk first), limited to top_k.
    - Filtered by tenant_id AND store_id (both live on the chunk row).
    - A store_id owned by ANOTHER tenant returns [] (no leak) — the router maps [] on an
      unknown/cross-tenant store to a uniform 404 (asserted at the router layer in build).

RED reason: `VectorStoreFileRepository` has no `search_chunks` method yet -> calling it
raises AttributeError. Missing-implementation red on the live, imported repository against
real Postgres (gateway_test_file_search_tool) with the pgvector extension + HNSW index.

Run only this file:
  cd apps/gateway && GATEWAY_TEST_DATABASE_URL=postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test_file_search_tool \
    uv run pytest tests/file_search_tool/test_retrieval_repo.py -q --override-ini="addopts="
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.vector_stores.infrastructure.file_repository import VectorStoreFileRepository
from gateway.vector_stores.infrastructure.orm import (
    VectorStoreChunkRow,
    VectorStoreFileRow,
    VectorStoreRow,
)

pytestmark = pytest.mark.asyncio

_DIM = 1536


def _unit_vec(hot: int) -> list[float]:
    """A 1536-dim one-hot vector — cosine similarity is 1.0 with itself, 0.0 with any other."""
    v = [0.0] * _DIM
    v[hot] = 1.0
    return v


async def _seed_store_with_chunks(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    chunks: list[tuple[str, list[float]]],
) -> uuid.UUID:
    store = VectorStoreRow(tenant_id=tenant_id, key_id=uuid.uuid4(), name="docs")
    session.add(store)
    await session.flush()
    file_row = VectorStoreFileRow(
        vector_store_id=store.id, tenant_id=tenant_id, file_id=uuid.uuid4(), status="completed"
    )
    session.add(file_row)
    await session.flush()
    for idx, (content, embedding) in enumerate(chunks):
        session.add(
            VectorStoreChunkRow(
                vector_store_file_id=file_row.id,
                vector_store_id=store.id,
                tenant_id=tenant_id,
                chunk_index=idx,
                content=content,
                embedding=embedding,
            )
        )
    await session.flush()
    return store.id


async def test_search_returns_nearest_chunks_top_k_ordered(db_session: AsyncSession) -> None:
    """top-k nearest-first: query near chunk-A returns A before B, limited to top_k."""
    tenant_id = uuid.uuid4()
    store_id = await _seed_store_with_chunks(
        db_session,
        tenant_id=tenant_id,
        chunks=[("alpha chunk", _unit_vec(0)), ("beta chunk", _unit_vec(1))],
    )
    repo = VectorStoreFileRepository(db_session)

    results: list[Any] = await repo.search_chunks(
        tenant_id=tenant_id, store_id=store_id, query_embedding=_unit_vec(0), top_k=1
    )

    assert len(results) == 1, f"top_k=1 must return exactly one chunk, got {len(results)}"
    assert results[0].content == "alpha chunk", (
        f"nearest chunk to query must rank first, got {results[0].content!r}"
    )


async def test_search_is_tenant_and_store_scoped_no_leak(db_session: AsyncSession) -> None:
    """A store owned by another tenant returns [] — retrieval never crosses the tenant line."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    store_b = await _seed_store_with_chunks(
        db_session, tenant_id=tenant_b, chunks=[("secret-b", _unit_vec(0))]
    )
    repo = VectorStoreFileRepository(db_session)

    # tenant A asks for tenant B's store id — must see nothing (router maps [] -> 404).
    leaked: list[Any] = await repo.search_chunks(
        tenant_id=tenant_a, store_id=store_b, query_embedding=_unit_vec(0), top_k=5
    )

    assert leaked == [], f"cross-tenant search must return [] with zero leak, got {leaked!r}"
