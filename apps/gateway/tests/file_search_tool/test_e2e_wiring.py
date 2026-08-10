"""BUILD-AUTHORED RED (CR v2 · F1) — file_search serviced END-TO-END through the REAL app.

Defect 2: the FileSearchGrounder is constructed nowhere in production — deps.py
(get_completion_use_case) and realtime_ws.py build CompletionUseCase WITHOUT
`file_search_grounder=`, so it defaults None, `_apply_file_search` no-ops, and an accepted
file_search tool is (a) never grounded / metered AND (b) forwarded RAW to the upstream
provider — the M5 leak regression vs. the old clean 400.

This test drives the REAL /v1/responses endpoint (deps.py wiring, NOT a hand-built
grounder) and proves, against the prod-constructed use case:
  (a) a file_search request grounds + meters exactly ONE per_query (model="file_search");
  (b) file_search is STRIPPED from the outbound provider body (M5 — no leak);
  (c) the LLM per_token record still fires beside it (billing not regressed).

RED on HEAD: grounder unwired -> no per_query record AND `upstream.last_payload["tools"]`
still carries the file_search tool (leak). GREEN once deps.py constructs + passes the
grounder from the app.state.vector_store_embedder + sessionmaker seams.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.vector_stores.infrastructure.orm import (
    VectorStoreChunkRow,
    VectorStoreFileRow,
    VectorStoreRow,
)
from gateway.vector_stores.wire_id import to_wire_id

from tests._polling import poll_until
from tests.responses_api_core.conftest import (
    RESPONSES,
    FakeCompletionUpstream,
    FakeUsageRecorder,
    auth_bearer,
    responses_payload,
    signup_and_login,
)

pytestmark = pytest.mark.asyncio

_DIM = 1536
ADMIN_KEYS = "/admin/keys"


def _unit_vec(hot: int) -> list[float]:
    v = [0.0] * _DIM
    v[hot] = 1.0
    return v


class _FakeEmbeddingClient:
    """app.state.vector_store_embedder stand-in — no network, no breaker; returns a fixed
    query vector aligned with the seeded chunk so the top-k cosine search hits it."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector
        self.calls: list[tuple[uuid.UUID, str, list[str]]] = []

    async def embed(
        self, tenant_id: uuid.UUID, model: str, texts: list[str]
    ) -> tuple[list[list[float]], dict[str, object] | None]:
        self.calls.append((tenant_id, model, list(texts)))
        return [self._vector], {"prompt_tokens": 3, "total_tokens": 3}


async def _make_key(client: httpx.AsyncClient) -> dict[str, str]:
    jwt, tenant_id = await signup_and_login(client, tenant_name="Echo", email="eve@echo.io")
    created = await client.post(ADMIN_KEYS, json={"name": "ci"}, headers=auth_bearer(jwt))
    assert created.status_code == 201, created.text
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
    }


async def _insert_model(db_session: AsyncSession, model_id: str) -> None:
    from sqlalchemy import text

    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, provider)"
            " VALUES (:i, :n, 128000, true, 'openrouter') ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": model_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots "
            "(id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at) "
            "VALUES (:id, :m, 0.0000025, 0.00001, now())"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()


async def _seed_store(
    db_session: AsyncSession, *, tenant_id: uuid.UUID, content: str, embedding: list[float]
) -> uuid.UUID:
    store = VectorStoreRow(tenant_id=tenant_id, key_id=uuid.uuid4(), name="docs")
    db_session.add(store)
    await db_session.flush()
    file_row = VectorStoreFileRow(
        vector_store_id=store.id, tenant_id=tenant_id, file_id=uuid.uuid4(), status="completed"
    )
    db_session.add(file_row)
    await db_session.flush()
    db_session.add(
        VectorStoreChunkRow(
            vector_store_file_id=file_row.id,
            vector_store_id=store.id,
            tenant_id=tenant_id,
            chunk_index=0,
            content=content,
            embedding=embedding,
        )
    )
    await db_session.flush()
    await db_session.commit()
    return store.id


async def test_file_search_serviced_end_to_end_no_upstream_leak(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    key = await _make_key(client)
    tenant_id = uuid.UUID(key["tenant_id"])
    await _insert_model(db_session, "openai/gpt-4o")
    store_id = await _seed_store(
        db_session,
        tenant_id=tenant_id,
        content="SSO is configured under Settings > Security.",
        embedding=_unit_vec(0),
    )

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder
    # The prod grounder embedding seam (default-ON adapter in prod lifespan) — a fake here.
    app.state.vector_store_embedder = _FakeEmbeddingClient(_unit_vec(0))

    resp = await client.post(
        RESPONSES,
        json=responses_payload(
            "openai/gpt-4o",
            tools=[{"type": "file_search", "vector_store_ids": [to_wire_id(store_id)]}],
        ),
        headers=auth_bearer(key["key"]),
    )

    assert resp.status_code == 200, resp.text
    # MIXED wait for the fire-and-forget usage-record tasks (LLM + per_query): BOTH
    # must land (positive), and file_search must be metered EXACTLY once (negative — a
    # second per_query record would be a double-bill, and polling alone would never
    # give it time to appear).

    async def _record_count() -> int:
        return len(recorder.records)

    await poll_until(_record_count, lambda n: n >= 2)
    # NEGATIVE WAIT: the exactly-once half of `models.count("file_search") == 1`.
    await asyncio.sleep(0.1)

    # (b) M5 — file_search STRIPPED from the outbound provider body (no leak).
    assert upstream.calls == 1, "exactly one upstream round-trip"
    out_tools = (upstream.last_payload or {}).get("tools") or []
    assert not any(
        isinstance(t, dict) and t.get("type") == "file_search" for t in out_tools
    ), f"SECURITY/M5: file_search leaked to the provider body: {out_tools!r}"

    # (a) per_query metered exactly once via the prod-constructed grounder.
    models = [r["model"] for r in recorder.records]
    assert models.count("file_search") == 1, (
        f"expected exactly one per_query file_search record through deps.py wiring, got {models!r}"
    )
    # (c) the LLM per_token record still fires beside it — billing not regressed.
    assert "openai/gpt-4o" in models, f"the served LLM record must still fire, got {models!r}"
