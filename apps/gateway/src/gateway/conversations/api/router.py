"""FastAPI router for the conversations domain (/v1/conversations).

Auth: every endpoint requires ``Authorization: Bearer sk-...`` via
``SqlAlchemyKeyAuthenticator`` → ``AuthzResult{tenant_id, key_id}``.
Missing / invalid key → 401 (AUTH_KEY_INVALID).

Tenant isolation: every repository call passes ``tenant_id`` from the
authenticated key. Cross-tenant / unknown / deleted resource → 404.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.conversations.infrastructure.repository import ConversationRepository
from gateway.core.db import get_session
from gateway.core.error_catalog import AUTH_KEY_EXPIRED, AUTH_KEY_INVALID
from gateway.core.errors import ProblemError
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator

conversations_router = APIRouter(tags=["conversations"])

# Singleton stateless hasher — safe to share
_hasher = Sha256SecretHasher()

# ---------------------------------------------------------------------------
# 404 helper
# ---------------------------------------------------------------------------

_CONV_NOT_FOUND = ProblemError(
    status=404,
    code="ERR_CONVERSATION_NOT_FOUND",
    title="Conversation not found",
)


def _not_found() -> ProblemError:
    return ProblemError(
        status=404,
        code="ERR_CONVERSATION_NOT_FOUND",
        title="Conversation not found",
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
    # Enforce key expiry for parity with the proxy/governance path — an expired
    # (but not yet revoked) key must NOT reach the conversations store. The
    # authenticate use-case checks revocation but not expiry, so gate it here.
    if authz.expires_at is not None:
        exp = authz.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp <= datetime.now(tz=UTC):
            raise AUTH_KEY_EXPIRED.exc() from None
    return authz


def _get_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationRepository:
    return ConversationRepository(session)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None)


class ConversationResponse(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationListItem(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int

    model_config = {"from_attributes": True}


class ConversationListResponse(BaseModel):
    data: list[ConversationListItem]
    limit: int
    offset: int


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetailResponse(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse]

    model_config = {"from_attributes": True}


class AppendMessageRequest(BaseModel):
    role: Annotated[str, Field(strict=True)]
    content: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        allowed = {"user", "assistant", "system"}
        if v not in allowed:
            raise ValueError(f"role must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("content must be a non-empty string (after stripping whitespace)")
        if len(v) > 1_000_000:
            raise ValueError("content exceeds maximum length of 1,000,000 characters")
        return v


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@conversations_router.post(
    "/v1/conversations",
    response_model=ConversationResponse,
    status_code=201,
)
async def create_conversation(
    body: CreateConversationRequest,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[ConversationRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationResponse:
    """Create a new conversation for the authenticated tenant."""
    row = await repo.create(
        tenant_id=authz.tenant_id,
        key_id=authz.key_id,
        title=body.title,
    )
    await session.commit()
    return ConversationResponse(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@conversations_router.get(
    "/v1/conversations",
    response_model=ConversationListResponse,
)
async def list_conversations(
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[ConversationRepository, Depends(_get_repo)],
    limit: int = 50,
    offset: int = 0,
) -> ConversationListResponse:
    """List active conversations for the authenticated tenant, newest first.

    limit: default 50, capped at 200.
    offset: must be >= 0.
    """
    effective_limit = min(max(limit, 1), 200)
    effective_offset = max(offset, 0)

    rows = await repo.list_active(
        tenant_id=authz.tenant_id,
        limit=effective_limit,
        offset=effective_offset,
    )

    return ConversationListResponse(
        data=[
            ConversationListItem(
                id=row.id,
                title=row.title,
                created_at=row.created_at,
                updated_at=row.updated_at,
                message_count=count,
            )
            for row, count in rows
        ],
        limit=effective_limit,
        offset=effective_offset,
    )


@conversations_router.get(
    "/v1/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
)
async def get_conversation(
    conversation_id: uuid.UUID,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[ConversationRepository, Depends(_get_repo)],
) -> ConversationDetailResponse:
    """Get conversation detail with messages.

    Returns 404 for unknown, cross-tenant, or soft-deleted conversations.
    """
    row = await repo.get_by_id(
        tenant_id=authz.tenant_id,
        conversation_id=conversation_id,
    )
    if row is None:
        raise _not_found()

    return ConversationDetailResponse(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        messages=[
            MessageResponse(
                id=msg.id,
                role=msg.role,
                content=msg.content,
                created_at=msg.created_at,
            )
            for msg in row.messages
        ],
    )


@conversations_router.post(
    "/v1/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=201,
)
async def append_message(
    conversation_id: uuid.UUID,
    body: AppendMessageRequest,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[ConversationRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MessageResponse:
    """Append a message to a conversation and bump updated_at in the same transaction.

    Returns 404 for unknown, cross-tenant, or soft-deleted conversations.
    Returns 422 for invalid role or empty/oversized content.
    """
    msg_row = await repo.append_message(
        tenant_id=authz.tenant_id,
        conversation_id=conversation_id,
        role=body.role,
        content=body.content,
    )
    if msg_row is None:
        raise _not_found()

    await session.commit()
    return MessageResponse(
        id=msg_row.id,
        role=msg_row.role,
        content=msg_row.content,
        created_at=msg_row.created_at,
    )


@conversations_router.delete(
    "/v1/conversations/{conversation_id}",
    status_code=204,
)
async def delete_conversation(
    conversation_id: uuid.UUID,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[ConversationRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
) -> None:
    """Soft-delete a conversation (sets deleted_at = now).

    Returns 404 for unknown, cross-tenant, or already-deleted conversations.
    """
    deleted = await repo.soft_delete(
        tenant_id=authz.tenant_id,
        conversation_id=conversation_id,
    )
    if not deleted:
        raise _not_found()

    await session.commit()
