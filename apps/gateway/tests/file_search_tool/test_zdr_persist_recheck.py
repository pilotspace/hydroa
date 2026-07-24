"""BUILD-AUTHORED RED — M6 (SECURITY): ZDR fail-closed at-rest, atomic re-check.

file_search injects 3rd-party document chunks into the stored Response payload, widening
the blast radius of the twice-HARD-STOPPED check-at-entry / persist-after-await ZDR TOCTOU
(see [[zdr-toctou-async-write-paths]]). The entry gate runs BEFORE the (slow) retrieval +
embedding round-trip; a tenant can flip tenants.zdr_enabled=true DURING that window. The
fix re-reads the ZDR flag ATOMICALLY inside the SAME transaction as the context_messages
insert (persist_stored_response) and fails closed — ZERO rows.

Both persist paths are covered because wrap_streaming_persist DELEGATES to
persist_stored_response, so the single atomic re-check guards non-stream AND stream.

The window is modelled with a SLOW double: a session wrapper that, before the atomic
re-check SELECT runs, AWAITS a concurrent flip that commits zdr_enabled=true on a separate
connection (read-committed -> the re-check sees it). An INSTANT double has zero window and
cannot reproduce this — exactly the gap dd5373a's SlowChunkEmbedder closed for ingestion.

RED reason: pre-fix persist_stored_response has NO ZDR re-check inside its insert
transaction -> it commits the stored_responses row despite the flip -> the count==0
assertion fails. (Also imports gateway.core.errors.ProblemError to assert the 403.)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.errors import ProblemError
from gateway.responses_store.application.persistence import (
    persist_stored_response,
    wrap_streaming_persist,
)

from tests.responses_state_store.conftest import _make_key

pytestmark = pytest.mark.asyncio

_CHUNK = [{"role": "system", "content": "GROUNDING: secret 3rd-party doc chunk text"}]


class _FlipOnFirstExecuteSession:
    """Wraps a real AsyncSession; before the FIRST execute() (the atomic raise_if_zdr
    re-check SELECT), AWAITS a callback that flips+commits zdr_enabled=true on a separate
    connection — a deterministic TOCTOU window an instant double cannot open."""

    def __init__(self, inner: AsyncSession, on_first: Any) -> None:
        self._inner = inner
        self._on_first = on_first
        self._fired = False

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        if not self._fired:
            self._fired = True
            await self._on_first()
        return await self._inner.execute(*args, **kwargs)

    def add(self, obj: Any) -> None:
        self._inner.add(obj)

    async def flush(self) -> None:
        await self._inner.flush()

    async def commit(self) -> None:
        await self._inner.commit()

    async def rollback(self) -> None:
        await self._inner.rollback()

    async def __aenter__(self) -> _FlipOnFirstExecuteSession:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._inner.__aexit__(*exc)


def _flip_factory(app: Any, tenant_id: str, *, flip: bool) -> Any:
    """A session_factory whose produced session flips ZDR (once, on the first execute)
    when ``flip`` is True; otherwise it is a plain pass-through (the control)."""

    async def _flip_zdr() -> None:
        async with app.state.sessionmaker() as s:
            await s.execute(
                text("UPDATE tenants SET zdr_enabled = true WHERE id = :tid"),
                {"tid": tenant_id},
            )
            await s.commit()

    async def _noop() -> None:
        return None

    def _factory() -> _FlipOnFirstExecuteSession:
        return _FlipOnFirstExecuteSession(
            app.state.sessionmaker(), _flip_zdr if flip else _noop
        )

    return _factory


async def _count_rows(db_session: AsyncSession, response_id: str) -> int:
    return int(
        (
            await db_session.execute(
                text("SELECT count(*) FROM stored_responses WHERE id = :rid"),
                {"rid": response_id},
            )
        ).scalar_one()
    )


async def test_zdr_atomic_recheck_slow_double_fail_closed(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    """M6 non-stream: a ZDR flip false->true DURING the persist window writes ZERO rows."""
    key = await _make_key(client, tenant_name="Acme", email="ada@acme.io")
    tenant_id = key["tenant_id"]
    response_id = f"resp_{uuid.uuid4().hex}"

    with pytest.raises(ProblemError) as exc_info:
        await persist_stored_response(
            _flip_factory(app, tenant_id, flip=True),
            response_id=response_id,
            tenant_id=uuid.UUID(tenant_id),
            key_id=uuid.UUID(key["key_id"]),
            model="gpt-4o",
            status="completed",
            previous_response_id=None,
            chain_depth=0,
            context_messages=_CHUNK,
            response_body={"id": response_id, "status": "completed"},
            usage=None,
        )

    assert exc_info.value.code == "ERR_ZDR_PAYLOAD_BLOCKED", (
        f"a ZDR flip must fail closed with the ZDR code, got {exc_info.value.code}"
    )
    assert await _count_rows(db_session, response_id) == 0, (
        "SECURITY: a ZDR flip during persist must write ZERO context_messages rows at-rest"
    )


async def test_zdr_no_flip_persists_one_row_control(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    """Control: without a flip the SAME harness persists exactly ONE row — proving the
    fail-closed test above is caused by the flip, not a broken harness (non-vacuous)."""
    key = await _make_key(client, tenant_name="Beta", email="bob@beta.io")
    tenant_id = key["tenant_id"]
    response_id = f"resp_{uuid.uuid4().hex}"

    await persist_stored_response(
        _flip_factory(app, tenant_id, flip=False),
        response_id=response_id,
        tenant_id=uuid.UUID(tenant_id),
        key_id=uuid.UUID(key["key_id"]),
        model="gpt-4o",
        status="completed",
        previous_response_id=None,
        chain_depth=0,
        context_messages=_CHUNK,
        response_body={"id": response_id, "status": "completed"},
        usage=None,
    )

    assert await _count_rows(db_session, response_id) == 1


async def test_zdr_flip_lands_after_recheck_select_fail_closed(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    """M6 (SECURITY) — closes the re-check-SELECT -> INSERT-commit window.

    The existing slow-double flips BEFORE the re-check SELECT: a PLAIN non-locking
    SELECT already catches that (a committed flip is visible under read-committed). The
    gap this test targets is the window AFTER the re-check reads and BEFORE the INSERT
    commits — a flip landing there escapes a plain SELECT entirely.

    Interleaving forced (deterministic, no fixed-sleep race on the assertion):
      1. a CONTROLLER connection issues `UPDATE tenants SET zdr_enabled=true` and HOLDS
         it uncommitted -> a FOR NO KEY UPDATE row lock on the tenant row;
      2. persist_stored_response starts (gated on `lock_held`). With the FOR UPDATE fix
         its re-check `SELECT ... FOR UPDATE` BLOCKS on that row lock; on the pre-fix
         PLAIN select it does NOT block, reads the stale zdr=false, and commits the row;
      3. the controller COMMITS the flip. The now-unblocked FOR UPDATE re-read observes
         zdr=true -> raises ERR_ZDR_PAYLOAD_BLOCKED -> the persist transaction rolls back
         -> ZERO rows.

    RED on HEAD: the plain SELECT reads stale-false in step 2, the INSERT commits during
    the controller's hold -> no raise, one row lands (both asserts fail). GREEN on the
    FOR UPDATE fix: the flip can never slip between the locked re-read and the INSERT.
    """
    key = await _make_key(client, tenant_name="Delta", email="deb@delta.io")
    tenant_id = key["tenant_id"]
    response_id = f"resp_{uuid.uuid4().hex}"

    lock_held = asyncio.Event()

    async def _controller() -> None:
        async with app.state.sessionmaker() as s:
            # Acquire (and HOLD, uncommitted) the tenant row lock with the flip pending.
            await s.execute(
                text("UPDATE tenants SET zdr_enabled = true WHERE id = :tid"),
                {"tid": tenant_id},
            )
            lock_held.set()
            # Give persist time to reach its re-check: under the fix it parks on the row
            # lock here; on HEAD it read stale-false and is already committing a row.
            await asyncio.sleep(0.5)
            await s.commit()  # release the lock -> the flip is now committed

    controller_task = asyncio.create_task(_controller())
    try:
        await lock_held.wait()
        with pytest.raises(ProblemError) as exc_info:
            await persist_stored_response(
                app.state.sessionmaker,
                response_id=response_id,
                tenant_id=uuid.UUID(tenant_id),
                key_id=uuid.UUID(key["key_id"]),
                model="gpt-4o",
                status="completed",
                previous_response_id=None,
                chain_depth=0,
                context_messages=_CHUNK,
                response_body={"id": response_id, "status": "completed"},
                usage=None,
            )
    finally:
        await controller_task

    assert exc_info.value.code == "ERR_ZDR_PAYLOAD_BLOCKED", (
        f"a flip landing between re-check and insert must fail closed, got {exc_info.value.code}"
    )
    assert await _count_rows(db_session, response_id) == 0, (
        "SECURITY: a ZDR flip in the re-check->commit window must write ZERO rows at-rest"
    )


async def test_zdr_atomic_recheck_streaming_path_fail_closed(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    """M6 stream: wrap_streaming_persist delegates to persist_stored_response, so the SAME
    atomic re-check guards the streaming path — a mid-window flip writes ZERO rows and the
    terminal frame is response.failed (never a fabricated completion, no chunk leak)."""
    key = await _make_key(client, tenant_name="Gamma", email="gus@gamma.io")
    tenant_id = key["tenant_id"]
    response_id = f"resp_{uuid.uuid4().hex}"

    completed = (
        b"event: response.completed\n"
        b'data: {"type":"response.completed","sequence_number":1,'
        b'"response":{"id":"' + response_id.encode() + b'","status":"completed"}}\n\n'
    )

    async def _src() -> Any:
        yield completed

    frames: list[bytes] = []
    async for frame in wrap_streaming_persist(
        _src(),
        session_factory=_flip_factory(app, tenant_id, flip=True),
        context_messages=_CHUNK,
        tenant_id=uuid.UUID(tenant_id),
        key_id=uuid.UUID(key["key_id"]),
        served_model="gpt-4o",
        previous_response_id=None,
        chain_depth=0,
    ):
        frames.append(frame)

    joined = b"".join(frames)
    assert b"response.failed" in joined, (
        "streaming ZDR flip must terminate as response.failed, not a fabricated completion"
    )
    assert b"secret 3rd-party doc chunk text" not in joined, "no chunk text may leak on failure"
    assert await _count_rows(db_session, response_id) == 0, (
        "SECURITY: a ZDR flip during streaming persist must write ZERO rows at-rest"
    )
