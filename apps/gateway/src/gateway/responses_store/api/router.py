"""FastAPI router for stored-response reads/deletes (responses-state-store PLAN.md §3).

  GET    /v1/responses/{response_id}  — the persisted Response object verbatim
  DELETE /v1/responses/{response_id}  — CASCADE hard-delete (row + tenant-scoped
                                        descendants) in ONE transaction

Auth: Bearer sk-... key → AuthzResult{tenant_id, key_id} (conversations idiom).
Missing / invalid key → 401 ERR_AUTH_INVALID_KEY (uniform-401 milestone rule).
Tenant isolation: every repository call passes tenant_id; a cross-tenant / unknown /
deleted id returns the SINGLE byte-identical 404 ERR_RESPONSE_NOT_FOUND — no leak.

NO usage_records write on either route (no provider cost — milestone billing rule).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import AUTH_KEY_EXPIRED, AUTH_KEY_INVALID, RESPONSE_NOT_FOUND
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator
from gateway.responses_store.infrastructure.repository import StoredResponseRepository

stored_responses_router = APIRouter(tags=["responses"])

# Singleton stateless hasher — safe to share (conversations idiom).
_hasher = Sha256SecretHasher()


def _extract_raw_key(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return ""


async def authenticate_bearer(session: AsyncSession, raw_key: str) -> AuthzResult:
    """Authenticate a raw Bearer sk-... key → AuthzResult (shared with the POST hook).

    Raises 401 ERR_AUTH_INVALID_KEY on any failure (missing / invalid), or
    ERR_AUTH_KEY_EXPIRED for an expired-but-unrevoked key — parity with the
    conversations store and the governance path.
    """
    repo = SqlAlchemyApiKeyRepository(session)
    authenticator = SqlAlchemyKeyAuthenticator(AuthzUseCase(repo, _hasher))
    try:
        authz = await authenticator.authenticate(raw_key)
    except InvalidApiKeyError:
        raise AUTH_KEY_INVALID.exc() from None
    if authz.expires_at is not None:
        exp = authz.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp <= datetime.now(tz=UTC):
            raise AUTH_KEY_EXPIRED.exc() from None
    return authz


async def _authenticate(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthzResult:
    return await authenticate_bearer(session, _extract_raw_key(request))


def _get_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StoredResponseRepository:
    return StoredResponseRepository(session)


@stored_responses_router.get("/v1/responses/{response_id}")
async def get_response(
    response_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[StoredResponseRepository, Depends(_get_repo)],
) -> Any:
    """Return the persisted Response object verbatim (200) or 404 (unknown/cross-tenant)."""
    body = await repo.get_response_body(tenant_id=authz.tenant_id, response_id=response_id)
    if body is None:
        raise RESPONSE_NOT_FOUND.exc()
    return JSONResponse(content=body, status_code=200)


@stored_responses_router.delete("/v1/responses/{response_id}")
async def delete_response(
    response_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[StoredResponseRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    """CASCADE hard-delete the row + tenant-scoped descendants in ONE transaction.

    200 → the pinned OpenAI wire shape {id, object, deleted}; 404 for unknown /
    cross-tenant / already-deleted (byte-identical to an unknown id).
    """
    deleted = await repo.cascade_delete(tenant_id=authz.tenant_id, response_id=response_id)
    if not deleted:
        raise RESPONSE_NOT_FOUND.exc()
    await session.commit()
    return JSONResponse(
        content={"id": response_id, "object": "response.deleted", "deleted": True},
        status_code=200,
    )
