"""GET /admin/compliance/art12-bundle — one-click Art. 12 record-keeping evidence bundle.

Contract (art12-record-keeping-preset TASK.md §3 — FROZEN @ v1):
  - Authorization: Bearer <JWT>, Permission.AUDIT_READ (owner/admin/operator/superadmin-own-
    tenant; billing_admin/viewer/member -> 403). Reuses the existing permission unchanged.
  - `since`/`until` (ISO-8601, both inclusive) are REQUIRED — unlike audit/export's optional
    filters, a dated period is a first-class Art. 12 cover-report field.
  - Deterministic, pinned cover: minted ONCE on the first call (no `bundle_token`) — reads
    the tenant's CURRENT name/residency_region/zdr_enabled/zdr_enabled_at/
    retention_window_days/guardrail_configs/default_tier exactly once, plus whether the
    tenant's plan currently includes "logs_explorer". Every later page of the SAME bundle
    walk echoes these PINNED values verbatim (never re-queried), carried inside the opaque
    `bundle_token` (no new server-side storage — extends the audit/logs export-cursor idiom).
  - Three independently keyset-paginated sections sharing ONE continuation token:
    `audit_events` (AuditEventItem, reused), `request_log_metadata` (LogListItem, reused,
    metadata-only), `usage_lineage` (NEW UsageLineageItem, via the new internal-only
    UsageRepository.list_for_tenant_keyset). One uniform `limit` (1..5000, default 1000)
    applies to all 3 sections per page.
  - ZDR honesty (M8): while the PINNED zdr_state.enabled is true, `request_log_metadata` is
    always `[]`/has_more=false with an explanatory `note` — never queried.
  - Plan-feature honesty, section-scoped (M9): if the PINNED "logs_explorer" entitlement is
    false, ONLY `request_log_metadata` is `[]`/has_more=false with a `note` — the endpoint
    itself never 403s for this reason; `audit_events`/`usage_lineage` are unaffected.
  - Read-only: the only write is a fire-and-forget audit-of-generation row (M11),
    action="compliance.art12_bundle", fail-open (existing `record_audit` writer, unchanged).
  - Bounded query time: one `asyncio.timeout` around the page's reads (plus the single
    tenant-row cover read on a mint call) -> ERR_EXPORT_TIMEOUT (504) on expiry (reused,
    no new catalog entry).
  - `bundle_token` binds to its minting period (M14): a continuation call's since/until MUST
    match the token's pinned period, else ERR_CURSOR_INVALID (422) — never silent re-pinning.

This is compliance/'s first module (CONVENTIONS.md clean-architecture layering) — pure
cross-context read composition over `audit/`, `logs/`, and `usage/`, with no domain state of
its own. The router calls the 3 repositories directly (no intervening use-case class),
mirroring `audit/api/router.py`'s own accepted precedent for a simple, non-mutating,
no-cross-aggregate-invariant read.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.audit.infrastructure.audit_repository import AuditRepository
from gateway.core.db import get_session
from gateway.core.error_catalog import CURSOR_INVALID, EXPORT_QUERY_TIMEOUT, PAYLOAD_INVALID
from gateway.core.errors import ProblemError
from gateway.core.ids import uuid7
from gateway.logs.api.logs_query_router import LogListItem
from gateway.logs.infrastructure.logs_repository import LogsRepository
from gateway.tenants.application.entitlements import check_plan_feature
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity
from gateway.tenants.infrastructure.orm import TenantRow
from gateway.usage.api.router import AuditEventItem
from gateway.usage.infrastructure.usage_repository import UsageLineageRow, UsageRepository

compliance_router = APIRouter(prefix="/admin/compliance", tags=["compliance"])

_DEFAULT_LIMIT = 1000
_MAX_LIMIT = 5000
_BUNDLE_READ_TIMEOUT_SECONDS = 30.0

_ZDR_NOTE_TEMPLATE = (
    "Zero-Data-Retention has been enabled since {enabled_at}; no request-log rows exist "
    "while ZDR is on."
)
_PLAN_FEATURE_NOTE = (
    "tenant plan does not include logs_explorer; audit_events and usage_lineage are unaffected"
)


# ---------------------------------------------------------------------------
# Response shapes (frozen — TASK.md §3)
# ---------------------------------------------------------------------------


class UsageLineageItem(BaseModel):
    """One usage_records row, billing-lineage projection (NEW item shape)."""

    model_config = ConfigDict(frozen=True)

    id: str
    key_id: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: str
    cost_basis: str
    usage_source: str
    tier_served: str
    status: int
    created_at: str


class PeriodModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    since: str
    until: str


class ZdrStateModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool
    enabled_at: str | None


class CoverModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    bundle_id: str
    generated_at: str
    tenant_id: str
    tenant_name: str
    period: PeriodModel
    residency_pin: str | None
    zdr_state: ZdrStateModel
    retention_window_days: int | None
    guardrail_configs_snapshot: dict[str, object]
    default_tier: str
    format_version: str = "1"


class AuditSectionModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[AuditEventItem]
    next_cursor: str | None
    has_more: bool
    note: str | None


class LogSectionModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[LogListItem]
    next_cursor: str | None
    has_more: bool
    note: str | None


class UsageSectionModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[UsageLineageItem]
    next_cursor: str | None
    has_more: bool
    note: str | None


class SectionsModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    audit_events: AuditSectionModel
    request_log_metadata: LogSectionModel
    usage_lineage: UsageSectionModel


class Art12BundleResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    cover: CoverModel
    sections: SectionsModel
    bundle_token: str | None


# ---------------------------------------------------------------------------
# Query-param parsing helpers (mirrors audit/api/router.py's local helpers)
# ---------------------------------------------------------------------------


def _parse_limit(limit: str | None) -> int:
    """Parse limit (1..5000, default 1000); non-integer or out-of-range -> PAYLOAD_INVALID."""
    if limit is None:
        return _DEFAULT_LIMIT
    try:
        parsed = int(limit)
    except ValueError:
        raise PAYLOAD_INVALID.exc() from None
    if parsed < 1 or parsed > _MAX_LIMIT:
        raise PAYLOAD_INVALID.exc()
    return parsed


def _parse_iso_datetime(raw: str) -> datetime:
    """Parse an ISO-8601 string; malformed -> PAYLOAD_INVALID."""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise PAYLOAD_INVALID.exc() from None


def _as_naive_utc(value: datetime) -> datetime:
    """Normalize a bound to naive UTC before binding (mirrors audit/api/router.py's
    `_as_naive_utc` verbatim — local copy per this codebase's own convention: never a
    cross-bounded-context import of a `_`-private helper)."""
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _parse_required_period(since: str | None, until: str | None) -> tuple[datetime, datetime]:
    """Both since AND until are REQUIRED (M3) — unlike audit/export's optional filters."""
    if since is None or until is None:
        raise PAYLOAD_INVALID.exc()
    parsed_since = _as_naive_utc(_parse_iso_datetime(since))
    parsed_until = _as_naive_utc(_parse_iso_datetime(until))
    if parsed_since > parsed_until:
        raise PAYLOAD_INVALID.exc()
    return parsed_since, parsed_until


# ---------------------------------------------------------------------------
# bundle_token codec — carries the PINNED cover snapshot + since/until + 3 section cursors.
# Extends the existing opaque-cursor idiom (audit/export, logs list) to also pin the cover
# (Issue #3 / M4/M7) — nothing new is persisted server-side.
# ---------------------------------------------------------------------------


class _SectionCursor(BaseModel):
    model_config = ConfigDict(frozen=True)

    cursor: tuple[str, str] | None  # (created_at.isoformat(), id) of the last-issued row
    exhausted: bool


class _TokenPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    bundle_id: str
    generated_at: str
    tenant_id: str
    tenant_name: str
    residency_pin: str | None
    zdr_enabled: bool
    zdr_enabled_at: str | None
    retention_window_days: int | None
    guardrail_configs_snapshot: dict[str, object]
    default_tier: str
    logs_explorer_entitled: bool
    since: str
    until: str
    audit_events: _SectionCursor
    request_log_metadata: _SectionCursor
    usage_lineage: _SectionCursor


def _encode_bundle_token(payload: _TokenPayload) -> str:
    raw = payload.model_dump_json().encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_bundle_token(raw: str) -> _TokenPayload:
    """Decode an opaque Art. 12 bundle_token. Any failure -> ERR_CURSOR_INVALID (R7) —
    distinct from ERR_PAYLOAD_INVALID, mirrors audit/export's `_decode_cursor` verbatim."""
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        return _TokenPayload.model_validate_json(decoded)
    except Exception:
        raise CURSOR_INVALID.exc() from None


def _section_cursor_tuple(section: _SectionCursor) -> tuple[datetime, uuid.UUID] | None:
    if section.cursor is None:
        return None
    created_at_raw, id_raw = section.cursor
    return datetime.fromisoformat(created_at_raw), uuid.UUID(id_raw)


def _encode_section_next_cursor(created_at: datetime, item_id: uuid.UUID | str) -> str:
    """Per-section opaque cursor surfaced for display (§3 `next_cursor`) — informational
    only; the ACTUAL continuation mechanism is the shared `bundle_token` (M7)."""
    payload = json.dumps({"created_at": created_at.isoformat(), "id": str(item_id)}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


# ---------------------------------------------------------------------------
# Cover mint
# ---------------------------------------------------------------------------


class _Cover(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    bundle_id: str
    generated_at: datetime
    tenant_id: uuid.UUID
    tenant_name: str
    residency_pin: str | None
    zdr_enabled: bool
    zdr_enabled_at: datetime | None
    retention_window_days: int | None
    guardrail_configs_snapshot: dict[str, object]
    default_tier: str
    logs_explorer_entitled: bool


async def _is_logs_explorer_entitled(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Boolean wrapper over the shared `check_plan_feature` gate (reused verbatim) — the
    bundle itself never 403s on this (M9), so a denial is captured as `False`, not raised."""
    try:
        await check_plan_feature(session, tenant_id, "logs_explorer")
    except ProblemError:
        return False
    return True


async def _mint_cover(session: AsyncSession, tenant_id: uuid.UUID) -> _Cover:
    """Read the tenant row + the logs_explorer entitlement exactly ONCE (M4/M9) — every
    later page of the SAME bundle walk echoes these values verbatim via the bundle_token,
    never re-querying `tenants` on a continuation call (this task's own Safety rule)."""
    row = (
        await session.execute(select(TenantRow).where(TenantRow.id == tenant_id))
    ).scalar_one_or_none()
    entitled = await _is_logs_explorer_entitled(session, tenant_id)

    if row is None:
        # Defensive fallback only — an authenticated caller always has a real tenant row.
        return _Cover(
            bundle_id=str(uuid7()),
            generated_at=_as_naive_utc(datetime.now(UTC)),
            tenant_id=tenant_id,
            tenant_name="",
            residency_pin=None,
            zdr_enabled=False,
            zdr_enabled_at=None,
            retention_window_days=None,
            guardrail_configs_snapshot={},
            default_tier="standard",
            logs_explorer_entitled=entitled,
        )

    return _Cover(
        bundle_id=str(uuid7()),
        generated_at=_as_naive_utc(datetime.now(UTC)),
        tenant_id=tenant_id,
        tenant_name=row.name,
        residency_pin=row.residency_region,
        zdr_enabled=row.zdr_enabled,
        zdr_enabled_at=row.zdr_enabled_at,
        retention_window_days=row.retention_window_days,
        guardrail_configs_snapshot=dict(row.guardrail_configs) if row.guardrail_configs else {},
        default_tier=row.default_tier,
        logs_explorer_entitled=entitled,
    )


def _cover_from_token(token: _TokenPayload) -> _Cover:
    return _Cover(
        bundle_id=token.bundle_id,
        generated_at=datetime.fromisoformat(token.generated_at),
        tenant_id=uuid.UUID(token.tenant_id),
        tenant_name=token.tenant_name,
        residency_pin=token.residency_pin,
        zdr_enabled=token.zdr_enabled,
        zdr_enabled_at=(
            datetime.fromisoformat(token.zdr_enabled_at) if token.zdr_enabled_at else None
        ),
        retention_window_days=token.retention_window_days,
        guardrail_configs_snapshot=token.guardrail_configs_snapshot,
        default_tier=token.default_tier,
        logs_explorer_entitled=token.logs_explorer_entitled,
    )


def _cover_to_model(cover: _Cover, since: datetime, until: datetime) -> CoverModel:
    return CoverModel(
        bundle_id=cover.bundle_id,
        generated_at=cover.generated_at.isoformat(),
        tenant_id=str(cover.tenant_id),
        tenant_name=cover.tenant_name,
        period=PeriodModel(since=since.isoformat(), until=until.isoformat()),
        residency_pin=cover.residency_pin,
        zdr_state=ZdrStateModel(
            enabled=cover.zdr_enabled,
            enabled_at=cover.zdr_enabled_at.isoformat() if cover.zdr_enabled_at else None,
        ),
        retention_window_days=cover.retention_window_days,
        guardrail_configs_snapshot=cover.guardrail_configs_snapshot,
        default_tier=cover.default_tier,
        format_version="1",
    )


# ---------------------------------------------------------------------------
# Generic keyset-section paging (duck-typed on .id/.created_at — AuditEvent, RequestLog,
# UsageLineageRow all carry both).
# ---------------------------------------------------------------------------


async def _page_section(
    *,
    exhausted: bool,
    cursor: tuple[datetime, uuid.UUID] | None,
    limit: int,
    fetch_page_plus_one: Any,
) -> tuple[list[Any], bool, tuple[datetime, uuid.UUID] | None, bool]:
    """Returns (page_items, has_more, new_cursor_for_token, new_exhausted).

    `new_cursor_for_token` is the (created_at, id) of the last row of THIS page when there
    are more rows to walk; once a section is exhausted it is never queried again (its stored
    cursor becomes irrelevant — M7: an exhausted section stays empty for the rest of the walk).
    """
    if exhausted:
        return [], False, cursor, True

    rows = list(await fetch_page_plus_one(cursor))
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    new_exhausted = not has_more
    new_cursor = (page_rows[-1].created_at, page_rows[-1].id) if page_rows else cursor
    return page_rows, has_more, new_cursor, new_exhausted


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@compliance_router.get("/art12-bundle", response_model=Art12BundleResponse)
async def get_art12_bundle(
    request: Request,
    identity: Annotated[Identity, require_permission(Permission.AUDIT_READ)],
    session: Annotated[AsyncSession, Depends(get_session)],
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
    limit: Annotated[str | None, Query()] = None,
    bundle_token: Annotated[str | None, Query()] = None,
) -> Art12BundleResponse:
    """Art. 12 record-keeping evidence bundle (FROZEN @ v1 — TASK.md §3).

    AUDIT_READ gated: owner/admin/operator/superadmin(-own-tenant) pass; billing_admin/
    viewer/member -> 403. Tenant-scoped in every section's own WHERE clause. Period
    (since/until) is REQUIRED. READ-ONLY; every successful page fires a fire-and-forget
    audit-of-generation write (M11).
    """
    tenant_id: uuid.UUID = identity.tenant_id
    parsed_limit = _parse_limit(limit)
    parsed_since, parsed_until = _parse_required_period(since, until)

    page_token_used = bundle_token is not None
    decoded_token: _TokenPayload | None = None
    if bundle_token is not None:
        decoded_token = _decode_bundle_token(bundle_token)
        # M14: a continuation call's since/until MUST match the token's pinned period.
        token_since = datetime.fromisoformat(decoded_token.since)
        token_until = datetime.fromisoformat(decoded_token.until)
        if token_since != parsed_since or token_until != parsed_until:
            raise CURSOR_INVALID.exc()

    try:
        async with asyncio.timeout(_BUNDLE_READ_TIMEOUT_SECONDS):
            if decoded_token is None:
                cover = await _mint_cover(session, tenant_id)
                audit_cursor: tuple[datetime, uuid.UUID] | None = None
                audit_exhausted = False
                logs_cursor: tuple[datetime, uuid.UUID] | None = None
                logs_exhausted = False
                usage_cursor: tuple[datetime, uuid.UUID] | None = None
                usage_exhausted = False
            else:
                cover = _cover_from_token(decoded_token)
                audit_cursor = _section_cursor_tuple(decoded_token.audit_events)
                audit_exhausted = decoded_token.audit_events.exhausted
                logs_cursor = _section_cursor_tuple(decoded_token.request_log_metadata)
                logs_exhausted = decoded_token.request_log_metadata.exhausted
                usage_cursor = _section_cursor_tuple(decoded_token.usage_lineage)
                usage_exhausted = decoded_token.usage_lineage.exhausted

            audit_repo = AuditRepository(session)
            logs_repo = LogsRepository(session)
            usage_repo = UsageRepository(session)

            async def _fetch_audit(
                c: tuple[datetime, uuid.UUID] | None,
            ) -> list[AuditEvent]:
                return await audit_repo.list_for_tenant_keyset(
                    tenant_id,
                    limit=parsed_limit + 1,
                    cursor=c,
                    since=parsed_since,
                    until=parsed_until,
                )

            async def _fetch_logs(
                c: tuple[datetime, uuid.UUID] | None,
            ) -> list[Any]:
                return await logs_repo.list_for_tenant_keyset(
                    tenant_id,
                    limit=parsed_limit + 1,
                    cursor=c,
                    since=parsed_since,
                    until=parsed_until,
                )

            async def _fetch_usage(
                c: tuple[datetime, uuid.UUID] | None,
            ) -> list[UsageLineageRow]:
                return await usage_repo.list_for_tenant_keyset(
                    tenant_id,
                    limit=parsed_limit + 1,
                    cursor=c,
                    since=parsed_since,
                    until=parsed_until,
                )

            (
                audit_items,
                audit_has_more,
                audit_new_cursor,
                audit_new_exhausted,
            ) = await _page_section(
                exhausted=audit_exhausted,
                cursor=audit_cursor,
                limit=parsed_limit,
                fetch_page_plus_one=_fetch_audit,
            )

            (
                usage_items,
                usage_has_more,
                usage_new_cursor,
                usage_new_exhausted,
            ) = await _page_section(
                exhausted=usage_exhausted,
                cursor=usage_cursor,
                limit=parsed_limit,
                fetch_page_plus_one=_fetch_usage,
            )

            # M8/M9 — request_log_metadata is a bundle-wide, PINNED honest-degrade: while
            # either condition holds it is NEVER queried, on ANY page of this walk.
            log_note: str | None = None
            if cover.zdr_enabled:
                enabled_at_str = (
                    cover.zdr_enabled_at.isoformat()
                    if cover.zdr_enabled_at
                    else ("an unknown time")
                )
                log_note = _ZDR_NOTE_TEMPLATE.format(enabled_at=enabled_at_str)
                log_items: list[Any] = []
                logs_has_more = False
                logs_new_cursor = logs_cursor
                logs_new_exhausted = True
            elif not cover.logs_explorer_entitled:
                log_note = _PLAN_FEATURE_NOTE
                log_items = []
                logs_has_more = False
                logs_new_cursor = logs_cursor
                logs_new_exhausted = True
            else:
                (
                    log_items,
                    logs_has_more,
                    logs_new_cursor,
                    logs_new_exhausted,
                ) = await _page_section(
                    exhausted=logs_exhausted,
                    cursor=logs_cursor,
                    limit=parsed_limit,
                    fetch_page_plus_one=_fetch_logs,
                )
    except TimeoutError:
        raise EXPORT_QUERY_TIMEOUT.exc() from None

    audit_section = AuditSectionModel(
        items=[_audit_event_to_item(e) for e in audit_items],
        next_cursor=(
            _encode_section_next_cursor(audit_new_cursor[0], audit_new_cursor[1])
            if audit_has_more and audit_new_cursor is not None
            else None
        ),
        has_more=audit_has_more,
        note=None,
    )
    log_section = LogSectionModel(
        items=[_log_to_item(rl) for rl in log_items],
        next_cursor=(
            _encode_section_next_cursor(logs_new_cursor[0], logs_new_cursor[1])
            if logs_has_more and logs_new_cursor is not None
            else None
        ),
        has_more=logs_has_more,
        note=log_note,
    )
    usage_section = UsageSectionModel(
        items=[_usage_to_item(u) for u in usage_items],
        next_cursor=(
            _encode_section_next_cursor(usage_new_cursor[0], usage_new_cursor[1])
            if usage_has_more and usage_new_cursor is not None
            else None
        ),
        has_more=usage_has_more,
        note=None,
    )

    any_has_more = audit_has_more or logs_has_more or usage_has_more
    new_token: str | None = None
    if any_has_more:
        new_token = _encode_bundle_token(
            _TokenPayload(
                bundle_id=cover.bundle_id,
                generated_at=cover.generated_at.isoformat(),
                tenant_id=str(cover.tenant_id),
                tenant_name=cover.tenant_name,
                residency_pin=cover.residency_pin,
                zdr_enabled=cover.zdr_enabled,
                zdr_enabled_at=(cover.zdr_enabled_at.isoformat() if cover.zdr_enabled_at else None),
                retention_window_days=cover.retention_window_days,
                guardrail_configs_snapshot=cover.guardrail_configs_snapshot,
                default_tier=cover.default_tier,
                logs_explorer_entitled=cover.logs_explorer_entitled,
                since=parsed_since.isoformat(),
                until=parsed_until.isoformat(),
                audit_events=_SectionCursor(
                    cursor=_cursor_to_pair(audit_new_cursor), exhausted=audit_new_exhausted
                ),
                request_log_metadata=_SectionCursor(
                    cursor=_cursor_to_pair(logs_new_cursor), exhausted=logs_new_exhausted
                ),
                usage_lineage=_SectionCursor(
                    cursor=_cursor_to_pair(usage_new_cursor), exhausted=usage_new_exhausted
                ),
            )
        )

    # Bundle generation is itself audited — fire-and-forget, fail-open (M11). Reuses the
    # existing writer's own separate-session/swallow-all contract verbatim.
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="compliance.art12_bundle",
                target_type=None,
                target_id=None,
                result="success",
                metadata={
                    "since": parsed_since.isoformat(),
                    "until": parsed_until.isoformat(),
                    "bundle_id": cover.bundle_id,
                    "page_token_used": page_token_used,
                    "limit": parsed_limit,
                    "row_counts": {
                        "audit_events": len(audit_items),
                        "request_log_metadata": len(log_items),
                        "usage_lineage": len(usage_items),
                    },
                },
                created_at=datetime.now(UTC),
            ),
        )
    )

    return Art12BundleResponse(
        cover=_cover_to_model(cover, parsed_since, parsed_until),
        sections=SectionsModel(
            audit_events=audit_section,
            request_log_metadata=log_section,
            usage_lineage=usage_section,
        ),
        bundle_token=new_token,
    )


def _cursor_to_pair(
    cursor: tuple[datetime, uuid.UUID] | None,
) -> tuple[str, str] | None:
    if cursor is None:
        return None
    created_at, item_id = cursor
    return created_at.isoformat(), str(item_id)


def _audit_event_to_item(event: AuditEvent) -> AuditEventItem:
    return AuditEventItem(
        id=str(event.id),
        actor_email=event.actor_email,
        action=event.action,
        target_type=event.target_type,
        target_id=event.target_id,
        result=event.result,
        metadata=event.metadata,
        created_at=(
            event.created_at.isoformat()
            if hasattr(event.created_at, "isoformat")
            else str(event.created_at)
        ),
    )


def _log_to_item(log: Any) -> LogListItem:
    return LogListItem(
        id=str(log.id),
        key_id=str(log.key_id),
        team_id=str(log.team_id) if log.team_id is not None else None,
        model_id=log.model_id,
        status_code=log.status_code,
        stream=log.stream,
        cached=log.cached,
        scrub_status=log.scrub_status,
        truncated=log.truncated,
        cost_usd=log.cost_usd,
        created_at=log.created_at.isoformat(),
        latency_ms=log.latency_ms,
        prompt_tokens=log.prompt_tokens,
        completion_tokens=log.completion_tokens,
        total_tokens=log.total_tokens,
    )


def _usage_to_item(row: UsageLineageRow) -> UsageLineageItem:
    return UsageLineageItem(
        id=str(row.id),
        key_id=str(row.key_id),
        model_id=row.model_id,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        cost_usd=str(row.cost_usd),
        cost_basis=row.cost_basis,
        usage_source=row.usage_source,
        tier_served=row.tier_served,
        status=row.status,
        created_at=row.created_at.isoformat(),
    )
