"""Red suite for openrouter-embeddings-routing — OER8/OER9/OER8b/OER10/OER11/OER12 (TASK.md §2).

SyncCatalogUseCase.execute() now fetches both the chat and embeddings OpenRouter
catalogs; a failure fetching embeddings degrades gracefully (embedding rows
untouched) while a chat-fetch failure still fails the whole sync, unchanged.
SqlAlchemyCatalogRepository.sync_catalog gains the embedding_models signal and
_upsert_model now writes modality.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.domain.entities import CatalogModel
from gateway.catalog.domain.errors import CatalogSourceUnavailableError
from gateway.catalog.infrastructure.repository import SqlAlchemyCatalogRepository

pytestmark = pytest.mark.asyncio

SYNC = "/internal/catalog/sync"

_GEMINI = CatalogModel(
    id="google/gemini-embedding-2",
    name="Gemini Embedding 2",
    context_length=2048,
    prompt_usd_per_token=1.5e-7,
    completion_usd_per_token=0.0,
    modality="embedding",
    provider="openrouter",
)
_OPUS = CatalogModel(
    id="anthropic/claude-opus-4",
    name="Claude Opus 4",
    context_length=200_000,
    prompt_usd_per_token=15e-6,
    completion_usd_per_token=75e-6,
    modality="chat",
    provider="openrouter",
)
_SONNET = CatalogModel(
    id="anthropic/claude-sonnet-4",
    name="Claude Sonnet 4",
    context_length=200_000,
    prompt_usd_per_token=3e-6,
    completion_usd_per_token=15e-6,
    modality="chat",
    provider="openrouter",
)


class _FakeSource:
    """CatalogSource stub with independently-failable chat/embeddings fetches."""

    def __init__(
        self,
        *,
        chat_models: list[CatalogModel] | None = None,
        embedding_models: list[CatalogModel] | None = None,
        chat_raises: bool = False,
        embeddings_raises: bool = False,
    ) -> None:
        self._chat = chat_models or []
        self._embeddings = embedding_models or []
        self._chat_raises = chat_raises
        self._embeddings_raises = embeddings_raises

    async def list_models(self) -> AsyncIterator[CatalogModel]:
        if self._chat_raises:
            raise CatalogSourceUnavailableError("fake chat upstream unavailable")
        for model in self._chat:
            yield model

    async def list_embedding_models(self) -> AsyncIterator[CatalogModel]:
        if self._embeddings_raises:
            raise CatalogSourceUnavailableError("fake embeddings upstream unavailable")
        for model in self._embeddings:
            yield model


def _install(app: Any, fake: _FakeSource) -> None:
    app.state.catalog_source = fake


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    body: dict[str, Any] = resp.json()
    assert body["code"] == code
    return body


async def _model_active(db: AsyncSession, model_id: str) -> bool | None:
    row = await db.execute(text("SELECT active FROM models WHERE id = :mid"), {"mid": model_id})
    result = row.scalar_one_or_none()
    return bool(result) if result is not None else None


async def _model_modality(db: AsyncSession, model_id: str) -> str | None:
    row = await db.execute(text("SELECT modality FROM models WHERE id = :mid"), {"mid": model_id})
    return row.scalar_one_or_none()


# ---------------------------------------------------------------------------
# OER8 — sync degrades gracefully when list_embedding_models() raises
# ---------------------------------------------------------------------------


async def test_sync_degrades_when_embeddings_fetch_raises(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    # Seed a previously-active embedding row via a first, fully-successful sync.
    _install(app, _FakeSource(chat_models=[_OPUS], embedding_models=[_GEMINI]))
    first = await client.post(SYNC)
    assert first.status_code == 200

    # Second sync: chat succeeds, embeddings fetch fails.
    _install(app, _FakeSource(chat_models=[_OPUS, _SONNET], embeddings_raises=True))
    resp = await client.post(SYNC)

    assert resp.status_code == 200, resp.text
    assert await _model_active(db_session, _OPUS.id) is True
    assert await _model_active(db_session, _SONNET.id) is True
    # The embedding row is untouched — still active, unaffected by the failed fetch.
    assert await _model_active(db_session, _GEMINI.id) is True


# ---------------------------------------------------------------------------
# OER9 — sync fails closed when list_models() (chat) raises
# ---------------------------------------------------------------------------


async def test_sync_fails_closed_when_chat_fetch_raises(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    _install(app, _FakeSource(embedding_models=[_GEMINI], chat_raises=True))

    resp = await client.post(SYNC)

    assert_problem(resp, 502, "ERR_UPSTREAM_UNAVAILABLE")
    rows = (await db_session.execute(text("SELECT count(*) FROM models"))).scalar_one()
    assert int(rows) == 0


# ---------------------------------------------------------------------------
# OER8b — a genuinely-retired embedding model IS deactivated when the fetch succeeds
# ---------------------------------------------------------------------------


async def test_sync_deactivates_genuinely_retired_embedding_model(
    db_session: AsyncSession,
) -> None:
    repo = SqlAlchemyCatalogRepository(db_session)
    old_embed = CatalogModel(
        id="old-embed-model",
        name="Old Embed",
        context_length=None,
        prompt_usd_per_token=1e-7,
        completion_usd_per_token=0.0,
        modality="embedding",
        provider="openrouter",
    )
    await repo.sync_catalog([_OPUS, _SONNET], embedding_models=[old_embed])

    # Next sync: old-embed-model is genuinely gone from the embeddings response;
    # _SONNET is genuinely gone from the chat response.
    await repo.sync_catalog([_OPUS], embedding_models=[_GEMINI])

    assert await _model_active(db_session, "old-embed-model") is False
    assert await _model_active(db_session, _SONNET.id) is False
    assert await _model_active(db_session, _OPUS.id) is True
    assert await _model_active(db_session, _GEMINI.id) is True


# ---------------------------------------------------------------------------
# OER10 — _upsert_model writes modality on first insert
# ---------------------------------------------------------------------------


async def test_upsert_writes_modality_on_insert(db_session: AsyncSession) -> None:
    repo = SqlAlchemyCatalogRepository(db_session)

    await repo.sync_catalog([], embedding_models=[_GEMINI])

    assert await _model_modality(db_session, _GEMINI.id) == "embedding"


# ---------------------------------------------------------------------------
# OER11 — _upsert_model updates modality on conflict (reclassification)
# ---------------------------------------------------------------------------


async def test_upsert_updates_modality_on_conflict(db_session: AsyncSession) -> None:
    repo = SqlAlchemyCatalogRepository(db_session)
    # First sync as chat (e.g. OpenRouter previously listed it under /models).
    reclassified = CatalogModel(
        id="some-id",
        name="Some Model",
        context_length=1000,
        prompt_usd_per_token=1e-6,
        completion_usd_per_token=2e-6,
        modality="chat",
        provider="openrouter",
    )
    await repo.sync_catalog([reclassified])
    assert await _model_modality(db_session, "some-id") == "chat"
    # Close the implicit read transaction so the next sync_catalog's explicit
    # session.begin() does not collide with SQLAlchemy 2.0 session autobegin.
    await db_session.commit()

    # Re-sync as embedding (reclassification), with an updated name/context_length.
    updated = CatalogModel(
        id="some-id",
        name="Some Model (embedding)",
        context_length=2000,
        prompt_usd_per_token=1e-6,
        completion_usd_per_token=2e-6,
        modality="embedding",
        provider="openrouter",
    )
    await repo.sync_catalog([], embedding_models=[updated])

    assert await _model_modality(db_session, "some-id") == "embedding"
    row = (
        await db_session.execute(
            text("SELECT name, context_length, active FROM models WHERE id = :mid"),
            {"mid": "some-id"},
        )
    ).one()
    assert row.name == "Some Model (embedding)"
    assert row.context_length == 2000
    assert bool(row.active) is True


# ---------------------------------------------------------------------------
# OER12 — provider column stays default "openrouter"
# ---------------------------------------------------------------------------


async def test_upsert_provider_stays_default(db_session: AsyncSession) -> None:
    repo = SqlAlchemyCatalogRepository(db_session)

    await repo.sync_catalog([_OPUS, _SONNET], embedding_models=[_GEMINI])

    for model_id in (_OPUS.id, _SONNET.id, _GEMINI.id):
        row = (
            await db_session.execute(
                text("SELECT provider FROM models WHERE id = :mid"), {"mid": model_id}
            )
        ).scalar_one()
        assert row == "openrouter"
