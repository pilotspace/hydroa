"""GET /admin/audit/export — compliance export over the immutable audit store.

Contract (compliance-export-api TASK.md §3 — FROZEN @ v1):
  - Authorization: Bearer <JWT>, Permission.AUDIT_READ (owner/admin/operator; other roles
    -> 403). Reuses the existing permission unchanged — no new enum member.
  - Tenant-scoped: only the caller's own tenant_id rows, under every filter combination.
  - Cursor pagination ONLY (no offset) — keyset on (created_at, id) DESC, append-safe
    under concurrent writes (structurally cannot skip/duplicate a row the way an
    offset-based page can on a live-appending table).
  - limit 1..5000, default 1000 (an archival/SIEM-connector cap, not the interactive
    GET /admin/audit cap of 100).
  - since/until (ISO-8601, both inclusive) + actor_email (exact, case-insensitive) filters.
  - Default body: NDJSON (one AuditEventItem-shaped JSON object per line), cursor
    delivered via X-Audit-Export-Next-Cursor / X-Audit-Export-Has-More response headers
    so every body line stays a pure, homogeneous audit record. `?format=json` opt-in
    returns {items, next_cursor, has_more} — deliberately no `total` (a COUNT over an
    arbitrary/unfiltered range has no archival-loop use).
  - Read-only: issues SELECT only. Every successful 200 fires one fire-and-forget
    record_audit call (action="audit.export"), fail-open — an audit-write failure never
    changes this response.
  - Bounded query time: asyncio.timeout around the DB read; a timeout raises the new
    catalog ERR_EXPORT_TIMEOUT (504) rather than propagating to an unhandled 500 (a
    deliberate hardening beyond the mirrored get_audit/get_alerts precedent, justified by
    this surface's much larger page ceiling).

This is audit's first `api/` layer (CONVENTIONS.md clean-architecture layering) — the
existing GET /admin/audit v1 (usage/api/router.py:728, FROZEN @ v1) is untouched; this is
a new sibling route, not a widening of that frozen envelope.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.audit.infrastructure.audit_repository import AuditRepository
from gateway.core.db import get_session
from gateway.core.error_catalog import CURSOR_INVALID, EXPORT_QUERY_TIMEOUT, PAYLOAD_INVALID
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity
from gateway.usage.api.router import AuditEventItem

audit_export_router = APIRouter(prefix="/admin/audit", tags=["audit"])

_EXPORT_DEFAULT_LIMIT = 1000
_EXPORT_MAX_LIMIT = 5000
_EXPORT_READ_TIMEOUT_SECONDS = 30.0
_CURSOR_NEXT_HEADER = "X-Audit-Export-Next-Cursor"
_CURSOR_HAS_MORE_HEADER = "X-Audit-Export-Has-More"


def _parse_limit(limit: str | None) -> int:
    """Parse limit (1..5000, default 1000); non-integer or out-of-range -> PAYLOAD_INVALID (M5)."""
    if limit is None:
        return _EXPORT_DEFAULT_LIMIT
    try:
        parsed = int(limit)
    except ValueError:
        raise PAYLOAD_INVALID.exc() from None
    if parsed < 1 or parsed > _EXPORT_MAX_LIMIT:
        raise PAYLOAD_INVALID.exc()
    return parsed


def _parse_format(fmt: str | None) -> str:
    """Default "ndjson"; only "ndjson"/"json" allowed (M8, R6)."""
    if fmt is None:
        return "ndjson"
    if fmt not in ("ndjson", "json"):
        raise PAYLOAD_INVALID.exc()
    return fmt


def _parse_iso_datetime(raw: str) -> datetime:
    """Parse an ISO-8601 string; malformed -> PAYLOAD_INVALID (R3). Mirrors the
    `fromisoformat(s.replace("Z", "+00:00"))` idiom used by keys/api/router.py and
    keys/api/platform_keys_router.py's own local `_datetime_or_none` helpers."""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise PAYLOAD_INVALID.exc() from None


def _as_naive_utc(value: datetime) -> datetime:
    """Normalize a `created_at` bound to naive UTC before binding.

    asyncpg expects a NAIVE UTC datetime against the test schema's `Mapped[datetime]`
    column (create_all maps it to a naive TIMESTAMP; prod's Alembic column is
    TIMESTAMPTZ). Mirrors usage/application/reconciliation.py:_as_naive_utc verbatim
    (documented gotcha per TASK.md §0 ground) — kept as a local copy rather than a
    cross-bounded-context import of a `_`-private helper (same "verbatim local copy"
    convention keys/api/platform_keys_router.py already uses for its own helpers).
    """
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _parse_time_range(
    since: str | None, until: str | None
) -> tuple[datetime | None, datetime | None]:
    """Parse since/until; reject malformed values (R3) or an inverted range (R4)."""
    parsed_since = _parse_iso_datetime(since) if since is not None else None
    parsed_until = _parse_iso_datetime(until) if until is not None else None
    if parsed_since is not None and parsed_until is not None and parsed_since > parsed_until:
        raise PAYLOAD_INVALID.exc()
    return parsed_since, parsed_until


def _encode_cursor(created_at: datetime, event_id: uuid.UUID) -> str:
    """Opaque base64url token carrying {created_at, id} of the last row of a page (M4)."""
    payload = json.dumps({"created_at": created_at.isoformat(), "id": str(event_id)}).encode(
        "utf-8"
    )
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    """Decode an opaque export cursor. Any failure -> ERR_CURSOR_INVALID (R7) — a code
    distinct from ERR_PAYLOAD_INVALID per §3, so a malformed cursor is never confused
    with a malformed limit/since/until/format in a client's error handling."""
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("ascii"))
        obj = json.loads(payload)
        created_at = datetime.fromisoformat(obj["created_at"])
        event_id = uuid.UUID(obj["id"])
    except Exception:
        raise CURSOR_INVALID.exc() from None
    return created_at, event_id


@audit_export_router.get("/export")
async def export_audit(
    request: Request,
    identity: Annotated[Identity, require_permission(Permission.AUDIT_READ)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
    actor_email: Annotated[str | None, Query()] = None,
    format: Annotated[str | None, Query()] = None,
) -> Response:
    """Compliance export over the caller's tenant audit trail (FROZEN @ v1 — TASK.md §3).

    AUDIT_READ gated: owner/admin/operator pass; billing_admin/viewer/member -> 403.
    Tenant-scoped, cursor-paginated (no offset — an unrecognized `offset` query param is
    simply never bound to anything here, so it is silently ignored per §2). READ-ONLY;
    every successful page also fires a fire-and-forget audit-of-export write (M11).
    """
    tenant_id: uuid.UUID = identity.tenant_id
    parsed_limit = _parse_limit(limit)
    resolved_format = _parse_format(format)
    parsed_since, parsed_until = _parse_time_range(since, until)

    cursor_used = cursor is not None
    cursor_tuple: tuple[datetime, uuid.UUID] | None = None
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        cursor_tuple = (_as_naive_utc(cursor_created_at), cursor_id)

    repo = AuditRepository(session)

    try:
        async with asyncio.timeout(_EXPORT_READ_TIMEOUT_SECONDS):
            events = await repo.list_for_tenant_keyset(
                tenant_id,
                limit=parsed_limit + 1,  # fetch one extra row to derive has_more (§3)
                cursor=cursor_tuple,
                since=_as_naive_utc(parsed_since) if parsed_since is not None else None,
                until=_as_naive_utc(parsed_until) if parsed_until is not None else None,
                actor_email=actor_email,
            )
    except TimeoutError:
        raise EXPORT_QUERY_TIMEOUT.exc() from None

    has_more = len(events) > parsed_limit
    page = events[:parsed_limit]
    next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None

    items = [
        AuditEventItem(
            id=str(e.id),
            actor_email=e.actor_email,
            actor_key_id=str(e.actor_key_id) if e.actor_key_id else None,
            actor_user_id=str(e.actor_user_id) if e.actor_user_id else None,
            actor_scim_token_id=(str(e.actor_scim_token_id) if e.actor_scim_token_id else None),
            action=e.action,
            target_type=e.target_type,
            target_id=e.target_id,
            result=e.result,
            metadata=e.metadata,
            created_at=(
                e.created_at.isoformat()
                if hasattr(e.created_at, "isoformat")
                else str(e.created_at)
            ),
        )
        for e in page
    ]

    # Export access is itself audited — fire-and-forget, fail-open (M11). Uses the
    # existing writer's own separate-session/swallow-all contract verbatim: an audit-write
    # failure here must never fail or delay this response.
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="audit.export",
                target_type=None,
                target_id=None,
                result="success",
                metadata={
                    "since": parsed_since.isoformat() if parsed_since is not None else None,
                    "until": parsed_until.isoformat() if parsed_until is not None else None,
                    "actor_email": actor_email,
                    "cursor_used": cursor_used,
                    "limit": parsed_limit,
                    "row_count": len(items),
                    "format": resolved_format,
                },
                created_at=datetime.now(UTC),
            ),
        )
    )

    if resolved_format == "json":
        return JSONResponse(
            content={
                "items": [item.model_dump() for item in items],
                "next_cursor": next_cursor,
                "has_more": has_more,
            }
        )

    # NDJSON (default): one line per record, every line newline-terminated (JSON-Lines
    # convention) — zero non-audit-row lines in the body, cursor lives only in headers.
    body = "".join(item.model_dump_json() + "\n" for item in items)
    headers = {_CURSOR_HAS_MORE_HEADER: "true" if has_more else "false"}
    if has_more and next_cursor is not None:
        headers[_CURSOR_NEXT_HEADER] = next_cursor
    return Response(content=body, media_type="application/x-ndjson", headers=headers)
