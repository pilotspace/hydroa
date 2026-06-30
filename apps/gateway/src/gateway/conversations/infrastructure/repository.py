"""SQLAlchemy repository for the conversations domain.

INVARIANT: every method takes ``tenant_id`` and every query filters on it.
A cross-tenant or unknown id always returns None / empty — NEVER 403/200.
The router maps None → 404 with zero data leak.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.conversations.infrastructure.orm import ConversationMessageRow, ConversationRow


class ConversationRepository:
    """All conversation persistence, scoped to a single SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        title: str | None,
    ) -> ConversationRow:
        """Insert a new conversation row and return it (server-side timestamps populated)."""
        row = ConversationRow(
            tenant_id=tenant_id,
            key_id=key_id,
            title=title,
        )
        self._session.add(row)
        await self._session.flush()  # populate server defaults (id, created_at, updated_at)
        await self._session.refresh(row)
        return row

    async def list_active(
        self,
        *,
        tenant_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> list[tuple[ConversationRow, int]]:
        """Return active (non-deleted) conversations for the tenant with message counts.

        Returns list of (ConversationRow, message_count) ordered by updated_at DESC.
        Bound params only — no SQL string interpolation.
        """
        # Subquery: count messages per conversation
        msg_count_sq = (
            select(
                ConversationMessageRow.conversation_id,
                func.count(ConversationMessageRow.id).label("msg_count"),
            )
            .group_by(ConversationMessageRow.conversation_id)
            .subquery()
        )

        stmt = (
            select(ConversationRow, func.coalesce(msg_count_sq.c.msg_count, 0).label("msg_count"))
            .outerjoin(msg_count_sq, ConversationRow.id == msg_count_sq.c.conversation_id)
            .where(
                ConversationRow.tenant_id == tenant_id,
                ConversationRow.deleted_at.is_(None),
            )
            .order_by(ConversationRow.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )

        result = await self._session.execute(stmt)
        return [(row, int(count)) for row, count in result.all()]

    async def get_by_id(
        self,
        *,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> ConversationRow | None:
        """Fetch a single active conversation by id, scoped to tenant_id.

        Returns None when the id is unknown, belongs to another tenant, or is soft-deleted.
        The ORM relationship ``messages`` is selectin-loaded automatically.
        """
        result = await self._session.execute(
            select(ConversationRow).where(
                ConversationRow.id == conversation_id,
                ConversationRow.tenant_id == tenant_id,
                ConversationRow.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def soft_delete(
        self,
        *,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> bool:
        """Soft-delete: set deleted_at = now() for the conversation scoped to tenant_id.

        Returns True if exactly one row was updated, False if zero rows matched
        (unknown id, belongs to another tenant, or already deleted — identical result, no leak).
        """
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            update(ConversationRow)
            .where(
                ConversationRow.id == conversation_id,
                ConversationRow.tenant_id == tenant_id,
                ConversationRow.deleted_at.is_(None),
            )
            .values(deleted_at=now)
            .returning(ConversationRow.id)
        )
        await self._session.flush()
        return result.scalar_one_or_none() is not None

    async def rename_title(
        self,
        *,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        title: str,
    ) -> ConversationRow | None:
        """Update the title of an active conversation, scoped to tenant_id.

        Bumps updated_at explicitly (raw UPDATE does not trigger ORM onupdate).
        Returns the updated ConversationRow on success, None when the id is
        unknown, belongs to another tenant, or is soft-deleted.
        """
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            update(ConversationRow)
            .where(
                ConversationRow.id == conversation_id,
                ConversationRow.tenant_id == tenant_id,
                ConversationRow.deleted_at.is_(None),
            )
            .values(title=title, updated_at=now)
            .returning(ConversationRow.id)
        )
        await self._session.flush()
        updated_id = result.scalar_one_or_none()
        if updated_id is None:
            return None
        # Re-fetch to return the full row (messages relationship loaded via selectin)
        fetch = await self._session.execute(
            select(ConversationRow).where(ConversationRow.id == updated_id)
        )
        return fetch.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def append_message(
        self,
        *,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        role: str,
        content: str,
    ) -> ConversationMessageRow | None:
        """Append a message to the conversation and bump updated_at in the SAME transaction.

        Returns the new message row on success, None when the conversation does not exist /
        belongs to another tenant / is soft-deleted.
        """
        # Verify ownership + active state (tenant-scoped) in the same session
        conv = await self._session.execute(
            select(ConversationRow).where(
                ConversationRow.id == conversation_id,
                ConversationRow.tenant_id == tenant_id,
                ConversationRow.deleted_at.is_(None),
            )
        )
        conv_row = conv.scalar_one_or_none()
        if conv_row is None:
            return None

        # Insert message
        msg_row = ConversationMessageRow(
            conversation_id=conversation_id,
            role=role,
            content=content,
        )
        self._session.add(msg_row)

        # Bump updated_at on the conversation in the same flush
        now = datetime.now(tz=UTC)
        await self._session.execute(
            update(ConversationRow)
            .where(
                ConversationRow.id == conversation_id,
                ConversationRow.tenant_id == tenant_id,
                ConversationRow.deleted_at.is_(None),
            )
            .values(updated_at=now)
        )

        await self._session.flush()
        await self._session.refresh(msg_row)
        return msg_row
