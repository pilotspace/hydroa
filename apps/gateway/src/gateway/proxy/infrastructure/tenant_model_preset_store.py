"""PostgreSQL implementation of TenantModelPresetStore.

§3 CONTRACT (tenant-preset-store TASK.md) — FROZEN @ v3.

INVARIANTS:
- Selector validation (``preset_name`` / ``alias_key`` non-empty, <= 64 chars,
  no colon) runs BEFORE any DB write — a rejected upsert never leaves a
  partial row.
- Target validation runs via a freshly-constructed ``SqlAlchemyModelChecker``
  BEFORE the write, in the SAME session/transaction as the write that follows.
- ``tenant_id`` filtering always happens in SQL — ``list``/``resolve``/``delete``
  never filter client-side, so cross-tenant isolation cannot regress.

``SqlAlchemyModelChecker`` (see ``gateway.proxy.infrastructure.model_checker``)
is constructed fresh per request over a request-scoped ``AsyncSession`` — its
own docstring says so explicitly ("A new instance is created per-request
(session-scoped)."), and every other call site in the codebase follows this
(``proxy/api/deps.py``, ``embeddings_deps.py``, ``images_deps.py``,
``audio_deps.py``, ``realtime_ws.py``, ``memory/api/router.py``). ``upsert``
does the same: it opens its own session, then constructs a checker over THAT
session, so ``is_active`` runs in the same transaction as the INSERT/UPDATE
that follows (validate-then-write, one connection, no partial row) — no
shared/boot-time checker instance, no injected dependency to keep in sync.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.proxy.domain.model_presets import ModelPresetError, TenantModelPreset
from gateway.proxy.domain.ports import ModelAccess
from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker
from gateway.proxy.infrastructure.orm import TenantModelPresetRow

#: Selector tokens (preset_name / alias_key) must be non-empty, <= this length,
#: and colon-free (":" is reserved as a future compound-selector separator).
_MAX_SELECTOR_TOKEN_LENGTH = 64

#: Postgres SQLSTATE for deadlock_detected (class 40 — transaction rollback).
_DEADLOCK_SQLSTATE = "40P01"
_DEADLOCK_MAX_ATTEMPTS = 3
_DEADLOCK_BACKOFF_BASE_S = 0.05


def _is_deadlock(exc: DBAPIError) -> bool:
    """True when `exc` wraps Postgres' deadlock_detected (SQLSTATE 40P01).

    Checked via `.orig.sqlstate` (set by the asyncpg dialect's exception
    translation) with a message-substring fallback for driver/version
    variance — a deadlock is a transient condition PostgreSQL's own docs
    recommend retrying, not a defect in the caller's query.
    """
    if getattr(exc.orig, "sqlstate", None) == _DEADLOCK_SQLSTATE:
        return True
    return "deadlock detected" in str(exc).lower()


async def _retry_on_deadlock[T](op: Callable[[], Awaitable[T]]) -> T:
    """Run `op` (a single-attempt async DB operation), retrying on a transient
    Postgres deadlock with a short jittered backoff.

    Design-for-failure: `upsert`/`delete` each open their own short-lived
    session/transaction, so a deadlock detected by Postgres always aborts and
    rolls back that ENTIRE transaction — re-running `op` from scratch (a
    fresh session) is always safe, never double-applies a partial write.
    """
    last_exc: DBAPIError | None = None
    for attempt in range(_DEADLOCK_MAX_ATTEMPTS):
        try:
            return await op()
        except DBAPIError as exc:
            if not _is_deadlock(exc) or attempt == _DEADLOCK_MAX_ATTEMPTS - 1:
                raise
            last_exc = exc
            await asyncio.sleep(random.uniform(0, _DEADLOCK_BACKOFF_BASE_S * (2**attempt)))  # noqa: S311 — jitter, not cryptographic
    raise AssertionError("unreachable") from last_exc  # pragma: no cover


def _validate_selector_token(value: str) -> None:
    """Reject empty / over-length / colon-bearing selector tokens.

    Raises ``ModelPresetError("ERR_PRESET_SELECTOR_INVALID")`` from None — no
    DB I/O has happened yet, so a reject here can never leave a partial row.
    """
    if not value or len(value) > _MAX_SELECTOR_TOKEN_LENGTH or ":" in value:
        raise ModelPresetError("ERR_PRESET_SELECTOR_INVALID") from None


class DbTenantModelPresetStore:
    """PostgreSQL store implementing ``TenantModelPresetStore``.

    Args:
        sessionmaker: An ``async_sessionmaker[AsyncSession]`` bound to the
            application database pool.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessionmaker = sessionmaker

    # ------------------------------------------------------------------
    # upsert
    # ------------------------------------------------------------------

    async def upsert(
        self,
        tenant_id: UUID,
        preset_name: str,
        alias_key: str,
        target_model: str,
    ) -> None:
        """Validate then persist the mapping in a single ON CONFLICT transaction.

        Guards (both run before any DB write):
        1. ``preset_name`` / ``alias_key`` fail selector-token validation →
           ``ERR_PRESET_SELECTOR_INVALID``.
        2. ``target_model`` is not active/visible for this ``tenant_id`` →
           ``ERR_PRESET_TARGET_UNKNOWN``.

        finetune-model-registry PLAN.md §3 M8 (advisor finding): guard 2 validates via
        ``check_for_tenant`` (tenant-scoped), NOT the tenant-blind ``is_active``. Once
        tenant-owned rows exist (e.g. a registered ft:* model), ``is_active`` alone
        would let a foreign tenant learn "this model id exists" by getting a DIFFERENT
        error than a truly-nonexistent id — a cross-tenant existence oracle via preset
        save. ``check_for_tenant`` folds "owned by someone else" and "doesn't exist" to
        the SAME ``ModelAccess.UNKNOWN`` -> the SAME ``ERR_PRESET_TARGET_UNKNOWN``.
        """
        # Guard 1: selector tokens — pure in-memory checks, no DB I/O.
        _validate_selector_token(preset_name)
        _validate_selector_token(alias_key)

        async def _do_upsert() -> None:
            async with self._sessionmaker() as session:
                # Guard 2: target validation. A fresh checker over THIS session (see
                # module docstring — matches every other call site in the codebase)
                # so it runs in the same transaction as the write below.
                model_checker = SqlAlchemyModelChecker(session)
                access = await model_checker.check_for_tenant(target_model, tenant_id)
                if access is not ModelAccess.ACTIVE:
                    raise ModelPresetError("ERR_PRESET_TARGET_UNKNOWN") from None

                stmt = (
                    pg_insert(TenantModelPresetRow)
                    .values(
                        tenant_id=tenant_id,
                        preset_name=preset_name,
                        alias_key=alias_key,
                        target_model=target_model,
                    )
                    .on_conflict_do_update(
                        index_elements=["tenant_id", "preset_name", "alias_key"],
                        set_={
                            "target_model": target_model,
                            "updated_at": text("now()"),
                        },
                    )
                )
                await session.execute(stmt)
                await session.commit()

        await _retry_on_deadlock(_do_upsert)

    # ------------------------------------------------------------------
    # resolve
    # ------------------------------------------------------------------

    async def resolve(
        self,
        tenant_id: UUID,
        preset_name: str,
        alias_key: str,
    ) -> str | None:
        """Return the mapped ``target_model``, or ``None`` if absent."""
        stmt = select(TenantModelPresetRow.target_model).where(
            TenantModelPresetRow.tenant_id == tenant_id,
            TenantModelPresetRow.preset_name == preset_name,
            TenantModelPresetRow.alias_key == alias_key,
        )

        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    async def list(
        self,
        tenant_id: UUID,
    ) -> list[TenantModelPreset]:
        """Return every preset mapping for ``tenant_id``, ordered by (preset_name, alias_key).

        The tenant filter is applied in SQL (``WHERE tenant_id = :tenant_id``) —
        never client-side — so cross-tenant isolation cannot regress.
        """
        stmt = (
            select(
                TenantModelPresetRow.preset_name,
                TenantModelPresetRow.alias_key,
                TenantModelPresetRow.target_model,
                TenantModelPresetRow.updated_at,
            )
            .where(TenantModelPresetRow.tenant_id == tenant_id)
            .order_by(TenantModelPresetRow.preset_name, TenantModelPresetRow.alias_key)
        )

        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            rows = result.all()

        return [
            TenantModelPreset(
                preset_name=preset_name,
                alias_key=alias_key,
                target_model=target_model,
                updated_at=updated_at,
            )
            for preset_name, alias_key, target_model, updated_at in rows
        ]

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------

    async def delete(
        self,
        tenant_id: UUID,
        preset_name: str,
        alias_key: str,
    ) -> bool:
        """Delete the mapping and return ``True`` iff a row existed."""
        stmt = (
            delete(TenantModelPresetRow)
            .where(
                TenantModelPresetRow.tenant_id == tenant_id,
                TenantModelPresetRow.preset_name == preset_name,
                TenantModelPresetRow.alias_key == alias_key,
            )
            .returning(TenantModelPresetRow.preset_name)
        )

        async def _do_delete() -> bool:
            async with self._sessionmaker() as session:
                result = await session.execute(stmt)
                await session.commit()
                return result.fetchone() is not None

        return await _retry_on_deadlock(_do_delete)
