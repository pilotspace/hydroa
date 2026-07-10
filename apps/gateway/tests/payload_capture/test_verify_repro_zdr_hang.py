"""VERIFY repro (add-verify, 2026-07-10): SqlAlchemyPayloadCapture.capture() has NO
bounded timeout around the ZdrOverridePort.is_zdr() call itself (sqlalchemy_capture.py:74).

The contract's `capture_persist_timeout_seconds` / asyncio.wait_for wrapping (§1 Must,
§3 capture_writer.py docstring) only bounds the scrub->truncate->INSERT sequence inside
persist_request_log — it does NOT cover the ZDR admission-gate check that runs BEFORE
persist_request_log is ever called. A ZdrOverridePort implementation that hangs (never
raises, never returns — distinct from the tested "raises" failure mode) causes capture()
itself to hang indefinitely. Because capture() is always dispatched via
asyncio.ensure_future (fire-and-forget), this does NOT affect the proxied response
(M2 holds) — but it DOES mean the task never completes, is never cancelled, and holds
its request/response body references forever: an unbounded task/memory leak that
compounds under any sustained ZDR-port degradation. This exact port is the FROZEN seam
the sibling `tenant-retention-zdr` task implements against, so this gap becomes live and
exploitable in production the moment that task's real (DB-backed) is_zdr lands with any
call that can stall (pool exhaustion, network partition, etc.) instead of cleanly raising.

Minimal, does not touch any frozen test or contract.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class _HangingZdrPort:
    """Never raises, never returns — distinct failure mode from _RaisingZdrPort."""

    async def is_zdr(self, tenant_id: uuid.UUID) -> bool:
        await asyncio.sleep(3600)  # effectively "hangs" relative to any test bound
        return False  # pragma: no cover


async def test_repro_zdr_port_hang_is_not_bounded(app: object, db_session: AsyncSession) -> None:
    from gateway.logs.infrastructure.sqlalchemy_capture import SqlAlchemyPayloadCapture

    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tenant_id), "name": "zdr-hang-tenant"},
    )
    await db_session.commit()

    port = SqlAlchemyPayloadCapture(
        session_factory=app.state.sessionmaker,  # type: ignore[attr-defined]
        zdr_port=_HangingZdrPort(),
        timeout_seconds=1.0,  # the capture-persist timeout — irrelevant here; the ZDR
        # check itself is never reached past, and nothing bounds THAT await.
    )

    # EXPECTED (§1 Must: the capture-persist call is bounded by an explicit timeout;
    # capture() must never raise and every failure path ends in "no row"/"metadata-only
    # row" promptly): capture() should return within roughly its own configured
    # timeout_seconds even when its ZdrOverridePort dependency hangs — a hang is exactly
    # as much a "misbehaving implementation" as a raise.
    #
    # ACTUAL: sqlalchemy_capture.py's `is_zdr = await self._zdr_port.is_zdr(tenant_id)`
    # has NO asyncio.wait_for/timeout wrapper — only the downstream DB-insert sequence in
    # persist_request_log is bounded. REPRO CONFIRMED: this assertion currently FAILS
    # (capture() does not return within 2x its own configured timeout).
    await asyncio.wait_for(
        port.capture(
            tenant_id=tenant_id,
            key_id=uuid.uuid4(),
            model="openai/gpt-4o-mini",
            request_body={"messages": [{"role": "user", "content": "hi"}]},
            response_body=None,
            status=200,
            stream=False,
            cached=False,
            guardrail_configs={},
        ),
        timeout=2.0,  # 2x the port's own configured timeout_seconds=1.0
    )
