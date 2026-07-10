"""Fire-and-forget request-log persist coroutine (payload-capture-store TASK.md §3, FROZEN @ v1).

This module owns persist_request_log, the coroutine that turns one proxied call's
(request_body, response_body) pair into exactly one scrubbed request_logs row (or a
metadata-only row on scrub/oversize failure, or NO row on a DB/timeout failure).

Contract:
  - Scrub-before-persist FIRST: mask_pii_in_messages (guardrail_evaluator.py, the SAME
    regex table evaluate_post uses) runs on both request and response message content
    before anything is serialized for storage. A scrub failure (the masking call raises)
    drops the payload and persists a METADATA-ONLY row (request_body=null,
    response_body=null, scrub_status="scrub_failed_metadata_only") — never the raw body.
  - Oversize handling: each message content string is truncated to max_field_bytes
    (BEFORE JSON serialization — never truncate an already-serialized JSONB blob, which
    would produce invalid JSON), appending "...[TRUNCATED]" and setting truncated=true.
    If the row's total serialized size still exceeds max_body_bytes after per-field
    truncation, the row falls back to metadata-only (scrub_status="oversize_metadata_only").
  - Bounded timeout: the whole scrub->truncate->INSERT sequence is wrapped in
    asyncio.wait_for(..., timeout=timeout_seconds) — an explicit seam alert_writer.py's
    precedent lacks. NEVER retried (a fire-and-forget best-effort write retried under a
    struggling store would amplify load during exactly the outage it should shed).
  - NEVER raises — every failure (scrub, size, DB, timeout) is caught and logged; the
    caller's proxied response is never affected (fail-OPEN for the proxied response;
    the row is simply dropped on any capture-store failure).

Wire: asyncio.ensure_future(payload_capture_port.capture(...)) at each hook site in
proxy/application/use_cases.py; the port implementation (SqlAlchemyPayloadCapture) owns
the ZDR-check + concurrency-admission gate and calls this coroutine to do the actual
scrub/truncate/insert work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.core.ids import uuid7
from gateway.proxy.infrastructure.guardrail_evaluator import mask_pii_in_messages

_log = logging.getLogger(__name__)

_TRUNCATION_MARKER = "...[TRUNCATED]"

_INSERT_REQUEST_LOG = text(
    """
    INSERT INTO request_logs
        (id, tenant_id, key_id, team_id, model_id, status_code, stream, cached,
         request_body, response_body, guardrail_verdict, scrub_status, truncated,
         cost_usd, created_at)
    VALUES
        (:id, :tenant_id, :key_id, :team_id, :model_id, :status_code, :stream, :cached,
         :request_body, :response_body, :guardrail_verdict, :scrub_status, :truncated,
         :cost_usd, now())
    """
)


def _extract_message_dicts(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalize a request or response body into a flat list of message-shaped dicts.

    Recognizes three shapes so the SAME extraction works for every hook site without
    the call site needing to pre-convert its natural body shape:
      - request shape:                {"messages": [...]}
      - non-streaming/cache-hit response shape (OpenAI-compatible): {"choices": [...]}
      - streaming-assembled response shape (this task's own synthesized shape):
        {"content": "<assembled text>"}
    Returns [] for None or an unrecognized shape — never raises.
    """
    if not isinstance(body, dict):
        return []
    messages = body.get("messages")
    if isinstance(messages, list):
        return [m for m in messages if isinstance(m, dict)]
    choices = body.get("choices")
    if isinstance(choices, list):
        out: list[dict[str, Any]] = []
        for choice in choices:
            if isinstance(choice, dict) and isinstance(choice.get("message"), dict):
                out.append(choice["message"])
        return out
    content = body.get("content")
    if isinstance(content, str):
        return [{"role": "assistant", "content": content}]
    return []


def _truncate_messages(
    messages: list[dict[str, Any]],
    max_field_bytes: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Truncate each message's content field to max_field_bytes (UTF-8), leaf-first.

    Returns (possibly-truncated messages, any_truncated). Truncation happens on the
    decoded string BEFORE JSON serialization — never on an already-serialized blob.
    """
    any_truncated = False
    out: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, str):
            out.append(msg)
            continue
        encoded = content.encode("utf-8")
        if len(encoded) <= max_field_bytes:
            out.append(msg)
            continue
        any_truncated = True
        cut = encoded[:max_field_bytes].decode("utf-8", errors="ignore")
        out.append({**msg, "content": cut + _TRUNCATION_MARKER})
    return out, any_truncated


async def _do_persist(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    team_id: uuid.UUID | None,
    model: str,
    request_body: dict[str, Any],
    response_body: dict[str, Any] | None,
    status: int,
    stream: bool,
    cached: bool,
    guardrail_configs: dict[str, Any],
    max_field_bytes: int,
    max_body_bytes: int,
) -> None:
    """Scrub -> truncate -> INSERT (no timeout wrapper — caller applies wait_for)."""
    row_id = uuid7()
    scrub_status = "scrubbed"
    truncated = False
    final_request_body: dict[str, Any] | None
    final_response_body: dict[str, Any] | None

    try:
        req_messages = _extract_message_dicts(request_body)
        masked_req = mask_pii_in_messages(req_messages, guardrail_configs)
        resp_messages = _extract_message_dicts(response_body) if response_body is not None else None
        masked_resp = (
            mask_pii_in_messages(resp_messages, guardrail_configs)
            if resp_messages is not None
            else None
        )
    except Exception as exc:
        _log.warning("capture_writer: scrub failed (metadata-only row)", exc_info=exc)
        await _insert(
            session_factory,
            row_id=row_id,
            tenant_id=tenant_id,
            key_id=key_id,
            team_id=team_id,
            model=model,
            status=status,
            stream=stream,
            cached=cached,
            request_body=None,
            response_body=None,
            scrub_status="scrub_failed_metadata_only",
            truncated=False,
        )
        return

    masked_req, req_truncated = _truncate_messages(masked_req, max_field_bytes)
    if masked_resp is not None:
        masked_resp, resp_truncated = _truncate_messages(masked_resp, max_field_bytes)
    else:
        resp_truncated = False
    truncated = req_truncated or resp_truncated
    if truncated:
        scrub_status = "scrubbed"

    final_request_body = {"messages": masked_req}
    final_response_body = {"messages": masked_resp} if masked_resp is not None else None

    total_bytes = len(json.dumps(final_request_body)) + (
        len(json.dumps(final_response_body)) if final_response_body is not None else 0
    )
    if total_bytes > max_body_bytes:
        await _insert(
            session_factory,
            row_id=row_id,
            tenant_id=tenant_id,
            key_id=key_id,
            team_id=team_id,
            model=model,
            status=status,
            stream=stream,
            cached=cached,
            request_body=None,
            response_body=None,
            scrub_status="oversize_metadata_only",
            truncated=True,
        )
        return

    await _insert(
        session_factory,
        row_id=row_id,
        tenant_id=tenant_id,
        key_id=key_id,
        team_id=team_id,
        model=model,
        status=status,
        stream=stream,
        cached=cached,
        request_body=final_request_body,
        response_body=final_response_body,
        scrub_status=scrub_status,
        truncated=truncated,
    )


async def _insert(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    row_id: uuid.UUID,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    team_id: uuid.UUID | None,
    model: str,
    status: int,
    stream: bool,
    cached: bool,
    request_body: dict[str, Any] | None,
    response_body: dict[str, Any] | None,
    scrub_status: str,
    truncated: bool,
) -> None:
    async with session_factory() as session:
        await session.execute(
            _INSERT_REQUEST_LOG,
            {
                "id": str(row_id),
                "tenant_id": str(tenant_id),
                "key_id": str(key_id),
                "team_id": str(team_id) if team_id is not None else None,
                "model_id": model,
                "status_code": status,
                "stream": stream,
                "cached": cached,
                "request_body": json.dumps(request_body) if request_body is not None else None,
                "response_body": json.dumps(response_body) if response_body is not None else None,
                "guardrail_verdict": None,
                "scrub_status": scrub_status,
                "truncated": truncated,
                "cost_usd": None,
            },
        )
        await session.commit()


async def persist_request_log(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    team_id: uuid.UUID | None = None,
    model: str,
    request_body: dict[str, Any],
    response_body: dict[str, Any] | None,
    status: int,
    stream: bool,
    cached: bool,
    guardrail_configs: dict[str, Any],
    timeout_seconds: float,
    max_field_bytes: int,
    max_body_bytes: int,
) -> None:
    """Persist one scrubbed request_logs row, bounded by timeout_seconds.

    NEVER raises — a timeout or any unexpected exception is logged and swallowed
    (no row is written; the caller's fire-and-forget task sees a clean return either
    way). Never retried.
    """
    try:
        await asyncio.wait_for(
            _do_persist(
                session_factory=session_factory,
                tenant_id=tenant_id,
                key_id=key_id,
                team_id=team_id,
                model=model,
                request_body=request_body,
                response_body=response_body,
                status=status,
                stream=stream,
                cached=cached,
                guardrail_configs=guardrail_configs,
                max_field_bytes=max_field_bytes,
                max_body_bytes=max_body_bytes,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        _log.warning(
            "capture_writer: persist timed out after %.2fs (dropped, no row)",
            timeout_seconds,
            extra={"tenant_id": str(tenant_id), "key_id": str(key_id)},
        )
    except Exception as exc:
        _log.warning(
            "capture_writer: persist failed (dropped, no row, swallowed)",
            exc_info=exc,
            extra={"tenant_id": str(tenant_id), "key_id": str(key_id)},
        )
