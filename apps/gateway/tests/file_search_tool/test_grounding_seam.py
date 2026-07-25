"""BUILD-AUTHORED RED — the shared file_search grounding seam (M5 / M7 / R:422 / R:503 / M3 parity).

The grounding+persist seam these exercise does NOT exist at direction — it is authored
first thing in build (PLAN.md §4, below-the-divider). They drive the REAL production
collaborator `FileSearchGrounder` (vector_stores/application/file_search.py):

  await grounder.ground(body=<chat body>, tenant_id=<uuid>, meter=<async callback>) -> bool

  * detect the file_search tool; absent -> return False, ZERO plumbing (M4)
  * empty/missing vector_store_ids -> MissingVectorStoreIds (422) BEFORE any retrieval/meter
  * embed the last user message via the PER-TENANT breaker; breaker-open/timeout ->
    UpstreamUnavailable (503), fail-closed, meter NEVER fired (R:upstream_unavailable, M7)
  * top-k cosine search (tenant+store scoped); inject grounding into body['messages'];
    STRIP file_search from body['tools'] (M5)
  * fire the injected `meter` callback EXACTLY ONCE per invocation — even on a 0-hit
    search (M3 parity); never on a 404/422/503 reject.

Retrieval runs against real Postgres (gateway_test_file_search_tool, pgvector + HNSW).
The embedding client is a FAKE (no network) — a fail double raises the real
UpstreamUnavailableError the per-tenant breaker/embed path raises in prod.

RED reason: `gateway.vector_stores.application.file_search` does not exist yet ->
ModuleNotFoundError on import (missing implementation, not a harness error).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.vector_stores.application.file_search import (
    FileSearchGrounder,
    MissingVectorStoreIds,
    UpstreamUnavailable,
)
from gateway.vector_stores.infrastructure.orm import (
    VectorStoreChunkRow,
    VectorStoreFileRow,
    VectorStoreRow,
)
from gateway.vector_stores.wire_id import to_wire_id

pytestmark = pytest.mark.asyncio

_DIM = 1536


def _unit_vec(hot: int) -> list[float]:
    v = [0.0] * _DIM
    v[hot] = 1.0
    return v


class _FakeEmbeddingClient:
    """Records the embed call; returns a fixed query vector. No network, no breaker."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector
        self.calls: list[tuple[uuid.UUID, str, list[str]]] = []

    async def embed(
        self, tenant_id: uuid.UUID, model: str, texts: list[str]
    ) -> tuple[list[list[float]], dict[str, object] | None]:
        self.calls.append((tenant_id, model, list(texts)))
        return [self._vector], {"prompt_tokens": 3, "total_tokens": 3}


class _FailEmbeddingClient:
    """embed() raises the real breaker-exhaustion error the embed path raises in prod."""

    def __init__(self) -> None:
        self.calls = 0

    async def embed(
        self, tenant_id: uuid.UUID, model: str, texts: list[str]
    ) -> tuple[list[list[float]], dict[str, object] | None]:
        self.calls += 1
        raise UpstreamUnavailableError("breaker open / timeout after one idempotent retry")


class _MeterSpy:
    def __init__(self) -> None:
        self.count = 0

    async def __call__(self) -> None:
        self.count += 1


async def _seed_store(
    session: AsyncSession, *, tenant_id: uuid.UUID, chunks: list[tuple[str, list[float]]]
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
    await session.commit()
    return store.id


def _body(store_wire_id: str, **tool_extra: Any) -> dict[str, Any]:
    tool: dict[str, Any] = {"type": "file_search", "vector_store_ids": [store_wire_id]}
    tool.update(tool_extra)
    return {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "What does the onboarding doc say about SSO?"}],
        "tools": [tool],
    }


async def test_file_search_stripped_from_upstream_payload(app: Any, db_session: AsyncSession) -> None:
    """M5: after grounding, the outgoing body carries NO file_search tool, and the
    retrieved chunk text is injected into messages; meter fires exactly once."""
    tenant_id = uuid.uuid4()
    store_id = await _seed_store(
        db_session, tenant_id=tenant_id, chunks=[("SSO is configured in Settings.", _unit_vec(0))]
    )
    grounder = FileSearchGrounder(
        embedding_client=_FakeEmbeddingClient(_unit_vec(0)),
        session_factory=app.state.sessionmaker,
    )
    body = _body(to_wire_id(store_id))
    meter = _MeterSpy()

    grounded = await grounder.ground(body=body, tenant_id=tenant_id, meter=meter)

    assert grounded is True
    tools = body.get("tools") or []
    assert not any(isinstance(t, dict) and t.get("type") == "file_search" for t in tools), (
        f"file_search leaked to the upstream body: {tools!r}"
    )
    injected = "".join(
        str(m.get("content", "")) for m in body["messages"] if isinstance(m, dict)
    )
    assert "SSO is configured in Settings." in injected, (
        f"retrieved chunk text was not injected into messages: {body['messages']!r}"
    )
    assert meter.count == 1, f"exactly one per_query meter expected, got {meter.count}"


async def test_missing_vector_store_ids_rejected(app: Any) -> None:
    """R:422 — an empty vector_store_ids rejects BEFORE any retrieval; meter never fires."""
    grounder = FileSearchGrounder(
        embedding_client=_FakeEmbeddingClient(_unit_vec(0)),
        session_factory=app.state.sessionmaker,
    )
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "file_search", "vector_store_ids": []}],
    }
    meter = _MeterSpy()

    with pytest.raises(MissingVectorStoreIds) as exc_info:
        await grounder.ground(body=body, tenant_id=uuid.uuid4(), meter=meter)

    assert exc_info.value.status == 422
    assert exc_info.value.code == "file_search_vector_store_ids_required"
    assert meter.count == 0, "a rejected request must never meter"


async def test_embedding_failure_fails_closed_no_bill(app: Any, db_session: AsyncSession) -> None:
    """R:upstream_unavailable + M7: a breaker-open/timeout embed -> 503, ZERO meter,
    no ungrounded silent answer (the tool is NOT stripped, so no partial grounding)."""
    tenant_id = uuid.uuid4()
    store_id = await _seed_store(
        db_session, tenant_id=tenant_id, chunks=[("doc", _unit_vec(0))]
    )
    fail_client = _FailEmbeddingClient()
    grounder = FileSearchGrounder(
        embedding_client=fail_client, session_factory=app.state.sessionmaker
    )
    body = _body(to_wire_id(store_id))
    meter = _MeterSpy()

    with pytest.raises(UpstreamUnavailable) as exc_info:
        await grounder.ground(body=body, tenant_id=tenant_id, meter=meter)

    assert exc_info.value.status == 503
    assert exc_info.value.code == "upstream_unavailable"
    assert fail_client.calls == 1
    assert meter.count == 0, "a 503 fail-closed must never meter (no bill)"


async def test_zero_hit_search_still_meters_one(app: Any, db_session: AsyncSession) -> None:
    """M3 parity: a valid store that matches 0 chunks STILL meters exactly one per_query."""
    tenant_id = uuid.uuid4()
    store_id = await _seed_store(db_session, tenant_id=tenant_id, chunks=[])  # empty store
    grounder = FileSearchGrounder(
        embedding_client=_FakeEmbeddingClient(_unit_vec(0)),
        session_factory=app.state.sessionmaker,
    )
    body = _body(to_wire_id(store_id))
    meter = _MeterSpy()

    grounded = await grounder.ground(body=body, tenant_id=tenant_id, meter=meter)

    assert grounded is True
    assert meter.count == 1, f"a 0-hit valid search still meters ONE, got {meter.count}"
    tools = body.get("tools") or []
    assert not any(isinstance(t, dict) and t.get("type") == "file_search" for t in tools)
