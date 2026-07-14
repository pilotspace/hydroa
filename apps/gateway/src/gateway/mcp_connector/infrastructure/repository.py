"""Raw-SQL CRUD for the MCP allow-list (mcp-connector-passthrough TASK.md §3).

Mirrors `tenants/api/residency_policy_router.py` (tenant-level) and
`keys/api/key_guardrail_router.py` (key-level) inline-session-query idiom exactly —
this repository does its OWN independent read/write for the CRUD surface; the hot
`/v1/mcp/call` auth path never touches it (that resolution rides the EXISTING LEFT
JOIN in `keys/infrastructure/repository.py:get_by_id()`, M4).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sized
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_MAX_SERVERS = 50


def _coerce_entries(raw: Any) -> list[dict[str, str]] | None:
    """Parse a raw JSONB value into a list of {url,label} dicts, or None if malformed.

    asyncpg may hand back a dict/list or a JSON string depending on driver version —
    same defensive parse as key_guardrail_router.py's own helper.
    """
    parsed = raw
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except Exception:
            return None
    if parsed is None:
        return None
    if not isinstance(parsed, list):
        return None
    out: list[dict[str, str]] = []
    for entry in parsed:
        if not (isinstance(entry, dict) and isinstance(entry.get("url"), str)):
            return None
        out.append({"url": entry["url"], "label": str(entry.get("label", ""))})
    return out


def validate_server_count(servers: Sized) -> bool:
    """R3: an allow-list PUT body of more than 50 entries is rejected.

    Accepts any sized collection (a list of `McpServerEntryBody` request models at
    the router boundary, or a list of `{url,label}` dicts once validated) — only the
    count matters here.
    """
    return len(servers) <= _MAX_SERVERS


class McpServerPolicyRepository:
    """Raw-SQL reads/writes over tenants.mcp_allowed_servers and
    api_keys.mcp_allowed_servers_override — the CRUD surface for §3's 5 routes.
    """

    async def get_tenant_servers(
        self, session: AsyncSession, tenant_id: uuid.UUID
    ) -> tuple[list[dict[str, str]], datetime | None] | None:
        """Returns (servers, updated_at), or None if the tenant row itself is missing."""
        row = (
            await session.execute(
                text(
                    "SELECT mcp_allowed_servers, mcp_allowed_servers_updated_at"
                    " FROM tenants WHERE id = :tid"
                ),
                {"tid": str(tenant_id)},
            )
        ).first()
        if row is None:
            return None
        servers = _coerce_entries(row[0]) or []
        return servers, row[1]

    async def put_tenant_servers(
        self, session: AsyncSession, tenant_id: uuid.UUID, servers: list[dict[str, str]]
    ) -> datetime:
        """Wholesale-replace the tenant allow-list; returns the new updated_at."""
        new_updated_at = datetime.now(UTC)
        await session.execute(
            text(
                "UPDATE tenants SET mcp_allowed_servers = :val::jsonb,"
                " mcp_allowed_servers_updated_at = :updated_at WHERE id = :tid"
            ),
            {"val": json.dumps(servers), "updated_at": new_updated_at, "tid": str(tenant_id)},
        )
        await session.commit()
        return new_updated_at

    async def get_tenant_allowed_urls(
        self, session: AsyncSession, tenant_id: uuid.UUID
    ) -> list[str]:
        """Fail-closed (M12) tenant-only URL list — used by the /v1/mcp/call use case
        as the fallback resolution for callers whose AuthzResult didn't carry
        mcp_allowed_servers (the agent-token path — CompositeKeyAuthenticator stays
        UNCHANGED per M14, so this is resolved here instead, not in the authenticator).
        """
        row = (
            await session.execute(
                text("SELECT mcp_allowed_servers FROM tenants WHERE id = :tid"),
                {"tid": str(tenant_id)},
            )
        ).first()
        if row is None:
            return []
        entries = _coerce_entries(row[0])
        if entries is None:
            return []
        return [e["url"] for e in entries]

    async def get_key_override(
        self, session: AsyncSession, *, key_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[dict[str, str]] | None | Literal["__not_found__"]:
        """Fetch the key's raw override for an ACTIVE, tenant-owned key.

        Returns "__not_found__" when the key is unknown, cross-tenant, or revoked (all
        three collapse to the same 404 — R8, mirrors key_guardrail_router.py precedent).
        """
        row = (
            await session.execute(
                text(
                    "SELECT mcp_allowed_servers_override FROM api_keys"
                    " WHERE id = :kid AND tenant_id = :tid AND revoked_at IS NULL"
                ),
                {"kid": str(key_id), "tid": str(tenant_id)},
            )
        ).first()
        if row is None:
            return "__not_found__"
        if row[0] is None:
            return None
        return _coerce_entries(row[0]) or []

    async def put_key_override(
        self,
        session: AsyncSession,
        *,
        key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        servers: list[dict[str, str]] | None,
    ) -> bool:
        """Wholesale-replace (or, if None, clear) the key's override.

        Returns False (no write occurred) on a race where the key was revoked/deleted
        between the caller's own fetch and this UPDATE — WHERE matched zero rows.
        """
        if servers is None:
            # Explicit null body clears back to tenant inheritance — a real SQL NULL,
            # never a JSON null literal (NULL vs [] is the meaningful distinction, M2).
            stmt = text(
                "UPDATE api_keys SET mcp_allowed_servers_override = NULL"
                " WHERE id = :kid AND tenant_id = :tid AND revoked_at IS NULL"
                " RETURNING id"
            )
            params: dict[str, Any] = {"kid": str(key_id), "tid": str(tenant_id)}
        else:
            stmt = text(
                "UPDATE api_keys SET mcp_allowed_servers_override = :val::jsonb"
                " WHERE id = :kid AND tenant_id = :tid AND revoked_at IS NULL"
                " RETURNING id"
            )
            params = {"val": json.dumps(servers), "kid": str(key_id), "tid": str(tenant_id)}
        result = await session.execute(stmt, params)
        found = result.fetchone() is not None
        if found:
            await session.commit()
        return found

    async def delete_key_override(
        self, session: AsyncSession, *, key_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> bool:
        """Clear the key's override (revert to tenant inheritance). Idempotent."""
        result = await session.execute(
            text(
                "UPDATE api_keys SET mcp_allowed_servers_override = NULL"
                " WHERE id = :kid AND tenant_id = :tid AND revoked_at IS NULL"
                " RETURNING id"
            ),
            {"kid": str(key_id), "tid": str(tenant_id)},
        )
        found = result.fetchone() is not None
        if found:
            await session.commit()
        return found


__all__ = ["McpServerPolicyRepository", "validate_server_count"]
