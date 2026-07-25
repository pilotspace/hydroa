"""Stored-response persistence orchestration (responses-state-store PLAN.md §3 M1/M2).

Both the non-stream and streaming store paths persist the row via a FRESH short-lived
session obtained from the app's session_factory — NOT the request session — because:
  * non-stream: keeps the persist transaction decoupled from the governance session; and
  * streaming: the request session is already closed by the time the stream generator
    reaches the terminal frame (the known trap, PLAN.md §3 Strategy item 4). The stream
    generator therefore MUST use its own session.

The row is written BEFORE the terminal ``response.completed`` frame is emitted; a
persistence failure at that point emits a terminal ``response.failed`` carrying
ERR_RESPONSES_STORE_FAILED instead of a fabricated completion (M2). No log line or error
detail ever embeds context_messages/response_body content (v22 no-payload-in-traceback).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.core.error_catalog import ZDR_PAYLOAD_BLOCKED
from gateway.responses_store.infrastructure.repository import StoredResponseRepository
from gateway.tenants.application.retention_policy import is_zdr_locked

_COMPLETED_PREFIX = b"event: response.completed\n"


#: LOCK-TAKING read of tenants.zdr_enabled, scoped to the session's already-open
#: (autobegun) transaction — held until it commits or rolls back.
#:
#: zdr-ingest-lock-heal (2026-07-25, M3): this was a LOCAL copy of the `FOR UPDATE`
#: read. It is now an alias for the SHARED primitive in
#: tenants/application/retention_policy.py. Behavior is identical; the reason for
#: collapsing them is that the duplicate was load-bearing and drifted — the
#: vector-store ingest worker needed the same lock, got a hand-written plain re-read
#: instead, and shipped the very TOCTOU this helper's own docstring warned about.
#: One definition site, reached by every path that persists payload after an await.
#:
#: NOTE the shared `is_zdr` / `raise_if_zdr` remain the plain NON-locking reads for
#: the six gated choke points — adding FOR UPDATE there would take a row lock on
#: every one of those unrelated hot writes.
_is_zdr_locked = is_zdr_locked


async def persist_stored_response(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    response_id: str,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    model: str,
    status: str,
    previous_response_id: str | None,
    chain_depth: int,
    context_messages: Any,
    response_body: Any,
    usage: Any,
) -> None:
    """Insert one stored_responses row in its own transaction (atomic; commits).

    SECURITY (M6, file-search-tool PLAN.md §3, CR v2): the entry-gate ZDR check ran BEFORE
    the slow retrieval/grounding/embedding round-trip, during which a tenant can flip
    zdr_enabled=true via their OWN retention-policy admin endpoint. file_search widens the
    blast radius — the context_messages now carry 3rd-party document chunk text. So re-read
    the ZDR flag with a LOCK-TAKING read (`SELECT ... FOR UPDATE`, `_is_zdr_locked`) inside
    the SAME transaction as the context_messages INSERT. A plain non-locking SELECT only
    catches a flip that committed BEFORE the read — the re-read -> INSERT-commit window
    stays open. The row lock closes it: a concurrent flip BLOCKS until this transaction
    commits/rolls back, so it can never land strictly between the re-read and the write.
    ZDR=true -> raise 403 ERR_ZDR_PAYLOAD_BLOCKED; the enclosing `async with` closes the
    session WITHOUT commit -> the autobegun transaction rolls back -> ZERO rows land
    at-rest — fail-closed. Streaming (wrap_streaming_persist) delegates here, so BOTH
    persist paths are guarded by the one locked re-check.
    """
    async with session_factory() as session:
        # First statement autobegins the transaction; the FOR UPDATE lock it takes is held
        # through the INSERT below until the explicit commit — decision + write are atomic.
        if await _is_zdr_locked(session, tenant_id):
            raise ZDR_PAYLOAD_BLOCKED.exc()
        repo = StoredResponseRepository(session)
        await repo.create(
            response_id=response_id,
            tenant_id=tenant_id,
            key_id=key_id,
            model=model,
            status=status,
            previous_response_id=previous_response_id,
            chain_depth=chain_depth,
            context_messages=context_messages,
            response_body=response_body,
            usage=usage,
        )
        await session.commit()


def _parse_frame(frame: bytes) -> dict[str, Any] | None:
    """Parse the JSON of an ``event: <name>\\ndata: <json>\\n\\n`` frame, or None."""
    marker = b"\ndata: "
    idx = frame.find(marker)
    if idx == -1:
        return None
    raw = frame[idx + len(marker) :].strip()
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _emit_frame(event: str, data: dict[str, Any]) -> bytes:
    return b"event: " + event.encode() + b"\ndata: " + json.dumps(data).encode() + b"\n\n"


async def wrap_streaming_persist(
    translated: AsyncIterator[bytes],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    context_messages: Any,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    served_model: str,
    previous_response_id: str | None,
    chain_depth: int,
) -> AsyncIterator[bytes]:
    """Persist the stored row BEFORE forwarding the terminal ``response.completed`` frame.

    On persist failure, forward a terminal ``response.failed`` (ERR_RESPONSES_STORE_FAILED)
    in place of the completion — never a fabricated success. Every other frame passes
    through byte-for-byte.
    """
    async for frame in translated:
        if not frame.startswith(_COMPLETED_PREFIX):
            yield frame
            continue
        data = _parse_frame(frame)
        response_obj = data.get("response") if isinstance(data, dict) else None
        if not isinstance(data, dict) or not isinstance(response_obj, dict):
            # Unparseable terminal — forward unchanged rather than lose the stream.
            yield frame
            continue
        # Echo the state truthfully on the persisted + emitted Response object.
        response_obj["store"] = True
        response_obj["previous_response_id"] = previous_response_id
        try:
            await persist_stored_response(
                session_factory,
                response_id=str(response_obj.get("id")),
                tenant_id=tenant_id,
                key_id=key_id,
                model=served_model,
                status=str(response_obj.get("status") or "completed"),
                previous_response_id=previous_response_id,
                chain_depth=chain_depth,
                context_messages=context_messages,
                response_body=response_obj,
                usage=response_obj.get("usage"),
            )
        except Exception:
            failed = {
                "type": "response.failed",
                "sequence_number": data.get("sequence_number"),
                "response": {
                    **response_obj,
                    "status": "failed",
                    "error": {
                        "code": "ERR_RESPONSES_STORE_FAILED",
                        "message": "failed to persist the stored response",
                    },
                },
            }
            yield _emit_frame("response.failed", failed)
            return
        yield _emit_frame("response.completed", data)
