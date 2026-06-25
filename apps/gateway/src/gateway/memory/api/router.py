"""FastAPI router for the memory domain (/v1/memories).

Auth: every endpoint requires ``Authorization: Bearer sk-...`` via
``SqlAlchemyKeyAuthenticator`` → ``AuthzResult{tenant_id, key_id}``.
Missing / invalid key → 401 (AUTH_KEY_INVALID).
Expired key → 401 (AUTH_KEY_EXPIRED).

Tenant isolation: every repository call passes ``tenant_id`` from the
authenticated key. Cross-tenant / unknown / deleted resource → 404.

Embedding: best-effort — a failure (any exception from the embed helper) leaves
the row's embedding NULL and the POST still returns 201. The embed helper reads
``request.app.state.memory_embedder`` (an async callable (raw_key, text) → list[float] | None)
when present; else falls back to the real EmbeddingsUseCase path.

Search: pure in-Python cosine over float8[] embeddings (no pgvector).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import get_session
from gateway.core.error_catalog import AUTH_KEY_EXPIRED, AUTH_KEY_INVALID
from gateway.core.errors import ProblemError
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.memory.application.search import rank
from gateway.memory.infrastructure.repository import MemoryRepository
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator

memories_router = APIRouter(tags=["memories"])

_log = logging.getLogger(__name__)

# Singleton stateless hasher — safe to share
_hasher = Sha256SecretHasher()

# ---------------------------------------------------------------------------
# 404 helper
# ---------------------------------------------------------------------------


def _not_found() -> ProblemError:
    return ProblemError(
        status=404,
        code="ERR_MEMORY_NOT_FOUND",
        title="Memory not found",
    )


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _extract_raw_key(request: Request) -> str:
    """Extract raw Bearer token from Authorization header, or empty string."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return ""


async def _authenticate(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthzResult:
    """Dependency: authenticate Bearer sk-... key → AuthzResult.

    Raises 401 on any failure (missing / invalid / expired).
    """
    raw_key = _extract_raw_key(request)
    repo = SqlAlchemyApiKeyRepository(session)
    authz_use_case = AuthzUseCase(repo, _hasher)
    authenticator = SqlAlchemyKeyAuthenticator(authz_use_case)
    try:
        authz = await authenticator.authenticate(raw_key)
    except InvalidApiKeyError:
        raise AUTH_KEY_INVALID.exc() from None
    # Enforce key expiry — mirrors conversations router expiry gate exactly
    if authz.expires_at is not None:
        exp = authz.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp <= datetime.now(tz=UTC):
            raise AUTH_KEY_EXPIRED.exc() from None
    return authz


def _get_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemoryRepository:
    return MemoryRepository(session)


def _get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


# ---------------------------------------------------------------------------
# Embed helper — BEST-EFFORT, never raises
# ---------------------------------------------------------------------------


async def _embed(request: Request, raw_key: str, text: str) -> list[float] | None:
    """Vectorize ``text`` using the injected or real embedder.

    Reads ``request.app.state.memory_embedder`` (async callable) if present;
    else falls back to the real EmbeddingsUseCase construction.

    ALWAYS returns None on any exception — embedding is best-effort.
    """
    # Test-injection seam: allows tests to install a stub without hitting a provider.
    custom_embedder = getattr(request.app.state, "memory_embedder", None)
    if custom_embedder is not None:
        try:
            result = await custom_embedder(raw_key, text)
            if result is None:
                return None
            return [float(x) for x in result]
        except Exception:
            _log.debug("memory_embedder stub raised; returning None (best-effort)")
            return None

    # Real path: build EmbeddingsUseCase like embeddings_deps.get_embeddings_use_case
    settings = _get_settings(request)
    if not settings.memory_embedding_model:
        return None

    try:
        # We use a fresh session for the embed call (separate from the request session)
        async with request.app.state.sessionmaker() as embed_session:
            from gateway.keys.application.use_cases import AuthzUseCase as _AuthzUseCase
            from gateway.keys.infrastructure.repository import (
                SqlAlchemyApiKeyRepository as _KeyRepo,
            )
            from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher as _Hasher
            from gateway.proxy.application.embeddings_use_case import EmbeddingsUseCase
            from gateway.proxy.application.governance import NonChatGovernance
            from gateway.proxy.infrastructure.key_authenticator import (
                SqlAlchemyKeyAuthenticator as _Auth,
            )
            from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker

            _h = _Hasher()
            _key_repo = _KeyRepo(embed_session)
            _authz = _AuthzUseCase(_key_repo, _h)
            _authenticator = _Auth(_authz)
            _model_checker = SqlAlchemyModelChecker(embed_session)
            _budget_guard = request.app.state.budget_guard
            _rate_limiter = getattr(request.app.state, "rate_limiter", None)
            _redis_client = getattr(_budget_guard, "_redis", None)
            _tenant_cred_resolver = getattr(
                request.app.state, "tenant_credential_resolver", None
            )

            governance = NonChatGovernance(
                authenticator=_authenticator,
                model_checker=_model_checker,
                budget_guard=_budget_guard,
                rate_limiter=_rate_limiter,
                redis_client=_redis_client,
                session_factory=request.app.state.sessionmaker,
            )
            uc = EmbeddingsUseCase(
                governance=governance,
                session=embed_session,
                tenant_credential_resolver=_tenant_cred_resolver,
            )
            status, body, _ = await uc.execute(
                raw_key=raw_key,
                body={
                    "model": settings.memory_embedding_model,
                    "input": text,
                },
                registry=request.app.state.provider_registry,
                usage_recorder=request.app.state.usage_recorder,
            )
            if status == 200:
                return [float(x) for x in body["data"][0]["embedding"]]
    except Exception:
        _log.debug("embed real-path raised; returning None (best-effort)")
    return None


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateMemoryRequest(BaseModel):
    content: str
    metadata: dict[str, object] | None = Field(default=None)

    @field_validator("content")
    @classmethod
    def _validate_content(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("content must be a non-empty string")
        return v


class MemoryCreateResponse(BaseModel):
    id: uuid.UUID
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MemoryListItem(BaseModel):
    id: uuid.UUID
    content: str
    created_at: datetime
    has_embedding: bool

    model_config = {"from_attributes": True}


class MemoryListResponse(BaseModel):
    data: list[MemoryListItem]
    limit: int
    offset: int


class SearchRequest(BaseModel):
    query: str
    top_k: int | None = Field(default=None)

    @field_validator("query")
    @classmethod
    def _validate_query(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must be a non-empty string")
        return v


class SearchResultItem(BaseModel):
    id: uuid.UUID
    content: str
    score: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SearchResponse(BaseModel):
    data: list[SearchResultItem]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@memories_router.post(
    "/v1/memories",
    response_model=MemoryCreateResponse,
    status_code=201,
)
async def create_memory(
    body: CreateMemoryRequest,
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[MemoryRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemoryCreateResponse:
    """Create a new memory for the authenticated tenant.

    The row is committed BEFORE embedding; an embed failure leaves
    embedding NULL and the POST still returns 201.
    """
    row = await repo.create(
        tenant_id=authz.tenant_id,
        key_id=authz.key_id,
        content=body.content,
        meta=body.metadata,
    )
    await session.commit()

    # Best-effort embedding — inline await so it completes before the response is returned.
    # Any exception is swallowed; failure leaves embedding NULL and the 201 is still returned.
    raw_key = _extract_raw_key(request)
    try:
        vec = await _embed(request, raw_key, body.content)
        if vec is not None:
            # Use a fresh session for the embedding update (separate transaction)
            async with request.app.state.sessionmaker() as embed_session:
                from gateway.memory.infrastructure.repository import (
                    MemoryRepository as _EmbedRepo,
                )

                embed_repo = _EmbedRepo(embed_session)
                await embed_repo.set_embedding(
                    tenant_id=authz.tenant_id,
                    memory_id=row.id,
                    embedding=vec,
                )
                await embed_session.commit()
    except Exception:
        _log.debug("embed-and-persist raised; row %s has null embedding (best-effort)", row.id)

    return MemoryCreateResponse(
        id=row.id,
        content=row.content,
        created_at=row.created_at,
    )


@memories_router.get(
    "/v1/memories",
    response_model=MemoryListResponse,
)
async def list_memories(
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[MemoryRepository, Depends(_get_repo)],
    limit: int = 50,
    offset: int = 0,
) -> MemoryListResponse:
    """List non-deleted memories for the authenticated tenant, newest first.

    limit: default 50, capped at 200. offset: must be >= 0.
    The raw embedding vector is NEVER returned.
    """
    effective_limit = min(max(limit, 1), 200)
    effective_offset = max(offset, 0)

    rows = await repo.list_active(
        tenant_id=authz.tenant_id,
        limit=effective_limit,
        offset=effective_offset,
    )

    return MemoryListResponse(
        data=[
            MemoryListItem(
                id=row.id,
                content=row.content,
                created_at=row.created_at,
                has_embedding=row.embedding is not None,
            )
            for row in rows
        ],
        limit=effective_limit,
        offset=effective_offset,
    )


@memories_router.post(
    "/v1/memories/search",
    response_model=SearchResponse,
)
async def search_memories(
    body: SearchRequest,
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[MemoryRepository, Depends(_get_repo)],
    settings: Annotated[Settings, Depends(_get_settings)],
) -> SearchResponse:
    """Search memories by semantic similarity (cosine) or substring fallback.

    top_k: clamped to 1..100, default cfg.memory_search_default_top_k.
    Ranked by cosine desc. Rows without an embedding skip cosine ranking
    (not returned in cosine mode). Text fallback (score=None) when embed fails.
    The raw embedding vector is NEVER returned.
    """
    default_k = settings.memory_search_default_top_k
    top_k = body.top_k if body.top_k is not None else default_k
    top_k = max(1, min(top_k, 100))

    raw_key = _extract_raw_key(request)

    # Embed the query — best-effort
    query_vec = await _embed(request, raw_key, body.query)

    # Load all non-deleted memories for this tenant
    rows = await repo.load_for_search(tenant_id=authz.tenant_id)

    results = rank(query_vec=query_vec, rows=rows, top_k=top_k, query_text=body.query)

    return SearchResponse(
        data=[
            SearchResultItem(
                id=row.id,
                content=row.content,
                score=score,
                created_at=row.created_at,
            )
            for row, score in results
        ]
    )


@memories_router.delete(
    "/v1/memories/{memory_id}",
    status_code=204,
)
async def delete_memory(
    memory_id: uuid.UUID,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[MemoryRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
) -> None:
    """Soft-delete a memory (sets deleted_at = now).

    Returns 404 for unknown, cross-tenant, or already-deleted memories.
    """
    deleted = await repo.soft_delete(
        tenant_id=authz.tenant_id,
        memory_id=memory_id,
    )
    if not deleted:
        raise _not_found()

    await session.commit()
