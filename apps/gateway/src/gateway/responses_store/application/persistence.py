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

from gateway.responses_store.infrastructure.repository import StoredResponseRepository

_COMPLETED_PREFIX = b"event: response.completed\n"


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
    """Insert one stored_responses row in its own transaction (atomic; commits)."""
    async with session_factory() as session:
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
