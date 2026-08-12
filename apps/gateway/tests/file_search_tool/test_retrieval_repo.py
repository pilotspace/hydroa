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


# ---------------------------------------------------------------------------
# todo #62 (SCALE) — HNSW post-filtering, and what is ACTUALLY true about it
# ---------------------------------------------------------------------------
# The todo's claim: HNSW applies the (tenant_id, vector_store_id) filter AFTER index
# traversal, so as `vector_store_chunks` grows across tenants a SMALL store's top-k
# silently returns fewer than top_k — a grounded-looking answer, a per_query bill, and
# no way to tell it from "your documents had nothing relevant". Its proposed remedy was
# `hnsw.ef_search` tuning.
#
# MEASURED on the real stack (pgvector 0.8.5 / PG16), and the premise does not survive:
#
#  1. The post-filtering is real. With the store-id btree index absent, a 4000-row
#     noisy neighbour reduced a 5-chunk store's top-20 to 2 rows
#     (`Index Scan using ..._embedding_idx ... Filter: (store_id = 2)`).
#  2. `hnsw.ef_search = 1000` did NOT fix it — still 2 of 5. A bigger candidate window
#     is still a window; it cannot reach rows that rank below it globally. The todo's
#     remedy would have been adopted, cost latency, and fixed nothing.
#     `hnsw.iterative_scan = strict_order` (pgvector >= 0.8) DID: 5 of 5, ordering intact.
#  3. But on THIS schema the planner does not choose that path for a small store.
#     `ix_vector_store_chunks_store` exists, so a selective store filter plans as an
#     exact filter-then-sort. Measured across store sizes in one 13,605-row table:
#         5 rows (0.0%) · 100 (0.7%) · 1500 (11.0%) · 4000 (29.4%)  -> exact sort, full recall
#         8000 rows (58.8%)                                        -> HNSW ordered scan
#     The two conditions for harm — HNSW chosen AND the store being a small share — are
#     mutually exclusive under the planner's own cost model. The store that gets the
#     HNSW path is the one whose own rows fill the candidate window anyway (20/20).
#
# So the small tenant is protected today — not by design, but by that btree index plus
# the cost model. That makes the index LOAD-BEARING FOR RETRIEVAL CORRECTNESS, which
# nothing recorded until now; the first test below is the guard. The residual exposure
# is (a) dropping/renaming that index, (b) stats drift after bulk ingest making a
# selective filter look non-selective, and (c) approximate ordering for a store large
# enough to get the HNSW path — a real ANN property, published rather than "fixed".
# search_chunks now asks for iterative scan (insurance for (a) and (b), no-op for the
# exact plan) and logs a shortfall whose signature cannot be a legitimately small store.


_NOISE_ROWS = 240  # >> the default hnsw.ef_search window of 40


def _dense_vec(rng: Any) -> list[float]:
    """An all-positive dense vector — cosine-CLOSE to any other all-positive vector,
    which is what lets a noisy neighbour store crowd out a sparse one."""
    return [rng.random() for _ in range(_DIM)]


async def test_a_small_store_search_is_planned_exactly_not_approximately(
    db_session: AsyncSession,
) -> None:
    """The btree store index is load-bearing for RECALL, not just for speed.

    Red against the counterfactual: with `ix_vector_store_chunks_store` dropped, the
    same query plans as an HNSW ordered scan with the store filter applied afterwards,
    and this store's 5 chunks come back as 2 (measured). This test fails if that index
    is ever dropped or renamed, or if the plan otherwise stops being exact for a store
    this selective.
    """
    import random  # noqa: PLC0415

    from sqlalchemy import text  # noqa: PLC0415

    rng = random.Random(20260812)  # noqa: S311 — test fixture noise, not crypto
    await _seed_store_with_chunks(
        db_session,
        tenant_id=uuid.uuid4(),
        chunks=[(f"noise-{i}", _dense_vec(rng)) for i in range(_NOISE_ROWS)],
    )
    small_tenant = uuid.uuid4()
    # Sparse one-hot chunks: cosine-far from the dense noise above, so all but the exact
    # match rank BELOW the entire noise cloud — the arrangement that truncates.
    small_chunks = [(f"doc-{i}", _unit_vec(i)) for i in range(5)]
    store_id = await _seed_store_with_chunks(
        db_session, tenant_id=small_tenant, chunks=small_chunks
    )

    plan = "\n".join(
        (
            await db_session.execute(
                text(
                    "EXPLAIN SELECT content FROM vector_store_chunks"
                    " WHERE tenant_id = :tid AND vector_store_id = :sid"
                    " ORDER BY embedding <=> :q LIMIT 20"
                ),
                {"tid": str(small_tenant), "sid": str(store_id), "q": str(_unit_vec(0))},
            )
        )
        .scalars()
        .all()
    )
    assert "_embedding_hnsw" not in plan, (
        "a SELECTIVE store filter is being served by the HNSW index, which post-filters"
        " and silently drops this store's lower-ranked chunks. The exact plan depends on"
        " ix_vector_store_chunks_store — has it been dropped, renamed, or has the table's"
        f" statistics drifted?\n{plan}"
    )

    repo = VectorStoreFileRepository(db_session)
    hits = await repo.search_chunks(
        tenant_id=small_tenant, store_id=store_id, query_embedding=_unit_vec(0), top_k=20
    )

    assert hits[0].content == "doc-0", "nearest-first ordering must hold"
    assert len(hits) == len(small_chunks), (
        f"the store has {len(small_chunks)} chunks and top_k is 20, but retrieval returned"
        f" {len(hits)}: {[h.content for h in hits]!r}"
    )


async def test_the_shortfall_signature_is_exact_not_a_heuristic() -> None:
    """The published bound needs a detector that can only fire on the real thing.

    Signature: fewer than top_k rows returned WHILE the store holds more chunks than
    were returned. A legitimately small store returns everything it has, so it can
    never satisfy both halves — which is what keeps this out of cry-wolf territory
    (an alert that fires on ordinary small stores gets muted, and then the real one is
    invisible too).
    """
    from gateway.vector_stores.infrastructure.file_repository import (  # noqa: PLC0415
        is_topk_shortfall,
    )

    # The defect: 20 asked for, store holds 900, only 3 came back.
    assert is_topk_shortfall(returned=3, total=900, top_k=20) is True
    # Boundary: one row short of top_k with rows to spare is still the defect.
    assert is_topk_shortfall(returned=19, total=900, top_k=20) is True
    # NOT the defect — a store smaller than top_k, fully returned.
    assert is_topk_shortfall(returned=4, total=4, top_k=20) is False
    assert is_topk_shortfall(returned=0, total=0, top_k=20) is False
    # NOT the defect — a full page. More chunks exist, but top_k was satisfied.
    assert is_topk_shortfall(returned=20, total=900, top_k=20) is False
    # Defensive: a total that is somehow stale/smaller than what was returned is not a
    # shortfall claim we can make.
    assert is_topk_shortfall(returned=5, total=3, top_k=20) is False


async def test_a_store_smaller_than_top_k_does_not_cry_wolf(
    db_session: AsyncSession, caplog: Any
) -> None:
    """End-to-end don't-cry-wolf: the ordinary small-store search stays quiet."""
    import logging  # noqa: PLC0415

    tenant_id = uuid.uuid4()
    store_id = await _seed_store_with_chunks(
        db_session,
        tenant_id=tenant_id,
        chunks=[(f"doc-{i}", _unit_vec(i)) for i in range(4)],
    )
    repo = VectorStoreFileRepository(db_session)

    with caplog.at_level(logging.WARNING):
        full = await repo.search_chunks(
            tenant_id=tenant_id, store_id=store_id, query_embedding=_unit_vec(0), top_k=2
        )
        short = await repo.search_chunks(
            tenant_id=tenant_id, store_id=store_id, query_embedding=_unit_vec(0), top_k=20
        )

    assert len(full) == 2
    assert len(short) == 4
    assert not [r for r in caplog.records if "shortfall" in r.getMessage()], (
        "neither a full top_k nor a store smaller than top_k is a truncation:"
        f" {[r.getMessage() for r in caplog.records]!r}"
    )


async def test_recall_survives_even_when_the_hnsw_path_is_taken(
    db_session: AsyncSession,
) -> None:
    """The counterfactual, in the suite: force the plan the planner takes when the
    protective index is gone / the stats have drifted / the store IS the table, and
    require full recall anyway.

    `enable_sort = off` + `enable_seqscan = off` leaves the HNSW ordered scan as the only
    viable path, reproducing exactly the plan measured with the btree index dropped
    (`Index Scan using ix_vector_store_chunks_embedding_hnsw ... Filter: ...`). Without
    iterative scan this returns 1-2 of the store's 5 chunks; with it, all 5, ordering
    intact. This is the test that is RED against the pre-fix tree — the plan guard above
    passes on it, which is exactly why it is not enough on its own.
    """
    import random  # noqa: PLC0415

    from sqlalchemy import text  # noqa: PLC0415

    rng = random.Random(20260812)  # noqa: S311 — test fixture noise, not crypto
    await _seed_store_with_chunks(
        db_session,
        tenant_id=uuid.uuid4(),
        chunks=[(f"noise-{i}", _dense_vec(rng)) for i in range(_NOISE_ROWS)],
    )
    small_tenant = uuid.uuid4()
    small_chunks = [(f"doc-{i}", _unit_vec(i)) for i in range(5)]
    store_id = await _seed_store_with_chunks(
        db_session, tenant_id=small_tenant, chunks=small_chunks
    )

    await db_session.execute(text("SET LOCAL enable_sort = off"))
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(
        (
            await db_session.execute(
                text(
                    "EXPLAIN SELECT content FROM vector_store_chunks"
                    " WHERE tenant_id = :tid AND vector_store_id = :sid"
                    " ORDER BY embedding <=> :q LIMIT 20"
                ),
                {"tid": str(small_tenant), "sid": str(store_id), "q": str(_unit_vec(0))},
            )
        )
        .scalars()
        .all()
    )
    # Vacuity guard: if the forcing did not land on the HNSW scan, this test proves nothing.
    assert "_embedding_hnsw" in plan, f"the HNSW path was not forced — vacuous:\n{plan}"
    assert "Filter:" in plan, f"the store/tenant filter is not post-index here:\n{plan}"

    repo = VectorStoreFileRepository(db_session)
    hits = await repo.search_chunks(
        tenant_id=small_tenant, store_id=store_id, query_embedding=_unit_vec(0), top_k=20
    )

    assert len(hits) == len(small_chunks), (
        f"HNSW post-filtering truncated a {len(small_chunks)}-chunk store's top-20 to"
        f" {len(hits)}: {[h.content for h in hits]!r} — the caller gets a grounded-looking"
        " answer and a per_query bill for a partial search"
    )
    assert hits[0].content == "doc-0", "iterative scan must keep the exact ordering"


async def test_an_old_pgvector_degrades_instead_of_failing_every_search(
    db_session: AsyncSession, monkeypatch: Any, caplog: Any
) -> None:
    """Design-for-failure: the iterative-scan request must never take retrieval down.

    On pgvector < 0.8 the GUC does not exist, and an unrecognized configuration
    parameter ABORTS the transaction — so a naive `SET LOCAL` would turn every
    file_search on that deployment into a 500, replacing a partial-recall bound with a
    total outage. The probe runs inside a SAVEPOINT for exactly this reason; here the
    unsupported server is simulated by pointing the statement at a GUC that does not
    exist, which is the same failure the real one produces.
    """
    import logging  # noqa: PLC0415

    from gateway.vector_stores.infrastructure import file_repository as fr  # noqa: PLC0415

    monkeypatch.setattr(fr, "_ITERATIVE_SCAN_SQL", "SET LOCAL hnsw.no_such_setting = 1")
    monkeypatch.setattr(fr._IterativeScan, "supported", None)  # noqa: SLF001

    tenant_id = uuid.uuid4()
    store_id = await _seed_store_with_chunks(
        db_session,
        tenant_id=tenant_id,
        chunks=[("first", _unit_vec(0)), ("second", _unit_vec(1))],
    )
    repo = VectorStoreFileRepository(db_session)

    with caplog.at_level(logging.ERROR):
        hits = await repo.search_chunks(
            tenant_id=tenant_id, store_id=store_id, query_embedding=_unit_vec(0), top_k=5
        )

    assert [h.content for h in hits] == ["first", "second"], (
        "retrieval must still work on a server without iterative scan"
    )
    assert fr._IterativeScan.supported is False, (  # noqa: SLF001
        "the capability must be remembered — re-probing per search would abort a"
        " transaction on every single retrieval"
    )
    assert any("iterative_scan_unavailable" in r.getMessage() for r in caplog.records), (
        "a deployment running with degraded recall must say so, once, loudly"
    )

    # And the very next search still succeeds (the cached False short-circuits it).
    again = await repo.search_chunks(
        tenant_id=tenant_id, store_id=store_id, query_embedding=_unit_vec(0), top_k=5
    )
    assert len(again) == 2
