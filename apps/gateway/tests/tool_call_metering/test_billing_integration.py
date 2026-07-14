"""DB+Redis integration RED suite — tool-call-metering bills through the SAME shared
billing path (TASK.md §4, FROZEN @ v1): RecordingUsageRecorder, the rate-card
resolver, catalog listing/sync exclusion, and InvoiceGenerator drill-down.

Drives MeteringToolCallObserver directly against a REAL RecordingUsageRecorder (real
Redis db 9 + real Postgres), then UsageLedgerFlusher.flush_once() to land the row —
mirrors tests/usage/test_usage_metering.py's own pattern. The DI-wiring path (real
app.state.mcp_tool_call_observer via POST /v1/mcp/call) is covered separately in
test_di_wiring.py.

RED before BUILD: `gateway.tool_call_metering.infrastructure.observer` does not exist
yet, so every import below fails — the honest missing-implementation red.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.tool_call_metering.conftest import (
    MCP_TOOL_CALL_MODEL_ID,
    bearer,
    seed_mcp_tool_call_pricing,
)

pytestmark = pytest.mark.asyncio


async def _seed_active_chat_model(db_session: AsyncSession, model_id: str = "openai/gpt-4o") -> None:
    """A normal, ACTIVE catalog model — needed alongside mcp_tool_call (active=false)
    so GET /catalog/models isn't simply empty (ERR_CATALOG_EMPTY) for these tests."""
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:id, 'GPT-4o', 128000, true) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": model_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:sid, :id, 0.0000025, 0.00001, now())"
        ),
        {"sid": str(uuid.uuid4()), "id": model_id},
    )
    await db_session.commit()


def _build_observer(app: Any, redis_client: Any) -> Any:
    from gateway.tool_call_metering.infrastructure.observer import MeteringToolCallObserver
    from gateway.usage.application.recorder import RecordingUsageRecorder

    recorder = RecordingUsageRecorder(redis=redis_client, session_factory=app.state.sessionmaker)
    app.state.usage_recorder = recorder
    return MeteringToolCallObserver(usage_recorder=recorder, redis=redis_client)


async def _flush(app: Any, redis_client: Any) -> None:
    from gateway.usage.application.flusher import UsageLedgerFlusher

    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()


async def _usage_rows(db_session: AsyncSession, tenant_id: str) -> list[Any]:
    rows = (
        await db_session.execute(
            text(
                "SELECT * FROM usage_records WHERE tenant_id = :tid AND model_id = :m"
                " ORDER BY created_at ASC"
            ),
            {"tid": tenant_id, "m": MCP_TOOL_CALL_MODEL_ID},
        )
    ).fetchall()
    return [r._mapping for r in rows]  # type: ignore[union-attr]


# ===========================================================================
# Scenario: cost computed via the shared rate-card resolver, Decimal end to end (M2, M3)
# ===========================================================================


async def test_cost_computed_via_shared_rate_card_resolver_decimal(
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
    redis_client: Any,
) -> None:
    await seed_mcp_tool_call_pricing(db_session)
    observer = _build_observer(app, redis_client)

    await observer.record(
        call_id=uuid.uuid4(),
        tenant_id=uuid.UUID(owner["tenant_id"]),
        key_id=uuid.UUID(owner["key_id"]),
        server_host="mcp.acme.example",
        tool_name="search",
        status="success",
        latency_ms=120,
    )
    await _flush(app, redis_client)

    rows = await _usage_rows(db_session, owner["tenant_id"])
    assert len(rows) == 1
    row = rows[0]
    # tenant markup_pct defaults to 20 (no rate-card override) -- region=global (1.0),
    # tier=standard (1.0): cost = 1 * 0.0025 * 1.20 = 0.0030
    expected_cost = Decimal("1") * Decimal("0.0025") * (Decimal("1") + Decimal("20") / Decimal("100"))
    assert Decimal(str(row["cost_usd"])) == expected_cost == Decimal("0.0030")
    assert row["cost_basis"] == "catalog"
    assert row["pricing_unit"] == "per_tool_call"
    assert Decimal(str(row["quantity"])) == Decimal("1")


# ===========================================================================
# Scenario: A tenant OWNER overrides tool-call markup via the rate-cards admin surface (M8)
# ===========================================================================


async def test_owner_overrides_tool_call_markup_via_existing_rate_cards_endpoint(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
    redis_client: Any,
) -> None:
    await seed_mcp_tool_call_pricing(db_session)

    resp = await client.put(
        f"/admin/rate-cards/{MCP_TOOL_CALL_MODEL_ID}",
        json={"markup_pct": "35"},
        headers=bearer(owner["jwt"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"] == MCP_TOOL_CALL_MODEL_ID
    assert Decimal(body["markup_pct"]) == Decimal("35")

    observer = _build_observer(app, redis_client)
    await observer.record(
        call_id=uuid.uuid4(),
        tenant_id=uuid.UUID(owner["tenant_id"]),
        key_id=uuid.UUID(owner["key_id"]),
        server_host="mcp.acme.example",
        tool_name="search",
        status="success",
        latency_ms=100,
    )
    await _flush(app, redis_client)

    rows = await _usage_rows(db_session, owner["tenant_id"])
    assert len(rows) == 1
    expected_cost = Decimal("1") * Decimal("0.0025") * (Decimal("1") + Decimal("35") / Decimal("100"))
    assert Decimal(str(rows[0]["cost_usd"])) == expected_cost


# ===========================================================================
# Scenario: mcp_tool_call is invisible in the tenant-facing catalog listing (M4, M5)
# ===========================================================================


async def test_mcp_tool_call_invisible_in_catalog_listing_but_still_prices(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
    redis_client: Any,
) -> None:
    await _seed_active_chat_model(db_session)
    await seed_mcp_tool_call_pricing(db_session, active=False)

    resp = await client.get("/v1/models", headers=bearer(owner["jwt"]))
    assert resp.status_code == 200, resp.text
    ids = {m["id"] for m in resp.json()["data"]}
    assert MCP_TOOL_CALL_MODEL_ID not in ids
    assert "openai/gpt-4o" in ids

    # Despite active=false, pricing still resolves (Scenario above's math, unchanged).
    observer = _build_observer(app, redis_client)
    await observer.record(
        call_id=uuid.uuid4(),
        tenant_id=uuid.UUID(owner["tenant_id"]),
        key_id=uuid.UUID(owner["key_id"]),
        server_host="mcp.acme.example",
        tool_name="search",
        status="success",
        latency_ms=100,
    )
    await _flush(app, redis_client)
    rows = await _usage_rows(db_session, owner["tenant_id"])
    assert len(rows) == 1
    assert Decimal(str(rows[0]["cost_usd"])) > Decimal("0")


# ===========================================================================
# Scenario: mcp_tool_call survives a catalog sync pass untouched (M6)
# ===========================================================================


async def test_mcp_tool_call_survives_catalog_sync_stale_deactivation_sweep(
    app: Any, db_session: AsyncSession
) -> None:
    from gateway.catalog.domain.entities import CatalogModel
    from gateway.catalog.infrastructure.repository import SqlAlchemyCatalogRepository

    await seed_mcp_tool_call_pricing(db_session, active=False)

    async with app.state.sessionmaker() as session:
        repo = SqlAlchemyCatalogRepository(session)
        # "mcp_tool_call" is absent from every incoming sync payload (never a real
        # upstream model) — the sweep only ever targets modality="chat" rows.
        await repo.sync_catalog(
            models=[
                CatalogModel(
                    id="openai/gpt-4o",
                    name="GPT-4o",
                    context_length=128000,
                    prompt_usd_per_token=0.0000025,
                    completion_usd_per_token=0.00001,
                )
            ],
            embedding_models=None,
        )

    row = (
        await db_session.execute(
            text("SELECT active FROM models WHERE id = :id"), {"id": MCP_TOOL_CALL_MODEL_ID}
        )
    ).fetchone()
    assert row is not None
    assert row[0] is False, "the sweep (WHERE modality='chat') must never touch a tool_call row"

    snapshot_count = (
        await db_session.execute(
            text("SELECT count(*) FROM pricing_snapshots WHERE model_id = :id"),
            {"id": MCP_TOOL_CALL_MODEL_ID},
        )
    ).scalar_one()
    assert snapshot_count == 1, "sync must not insert a new pricing snapshot for mcp_tool_call"


# ===========================================================================
# Scenario: Invoice generation drills down per (MCP server, tool) (M9)
# ===========================================================================


async def test_invoice_generation_drilldown_per_server_and_tool(
    app: Any, db_session: AsyncSession, owner: dict[str, str]
) -> None:
    from gateway.billing.application.invoice_generator import InvoiceGenerator

    tenant_id = uuid.UUID(owner["tenant_id"])
    key_id = uuid.UUID(owner["key_id"])

    async def _insert_row(tool_name: str) -> None:
        await db_session.execute(
            text(
                "INSERT INTO usage_records"
                " (id, tenant_id, key_id, model_id, cost_usd, status, raw, pricing_unit,"
                "  quantity, tags)"
                " VALUES (:id, :tid, :kid, :m, 0.003, 200, '{}'::jsonb, 'per_tool_call',"
                "  1, :tags::jsonb)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tid": str(tenant_id),
                "kid": str(key_id),
                "m": MCP_TOOL_CALL_MODEL_ID,
                "tags": json.dumps({"mcp_server": "mcp.acme.example", "mcp_tool": tool_name}),
            },
        )

    for _ in range(3):
        await _insert_row("search")
    for _ in range(2):
        await _insert_row("fetch")
    await db_session.commit()

    generator = InvoiceGenerator(session_factory=app.state.sessionmaker)
    import datetime

    invoice_id = await generator.generate_for_tenant(tenant_id, datetime.datetime.now(datetime.UTC))
    assert invoice_id is not None

    lines = (
        await db_session.execute(
            text(
                "SELECT tags, request_count FROM invoice_lines"
                " WHERE invoice_id = :iid AND model_id = :m ORDER BY request_count DESC"
            ),
            {"iid": str(invoice_id), "m": MCP_TOOL_CALL_MODEL_ID},
        )
    ).fetchall()
    assert len(lines) == 2
    by_tool = {dict(r._mapping)["tags"]["mcp_tool"]: dict(r._mapping)["request_count"] for r in lines}  # type: ignore[union-attr]
    assert by_tool == {"search": 3, "fetch": 2}


# ===========================================================================
# Scenario: Missing base price fails to an explained zero, never a silent one (M11, R1)
# ===========================================================================


async def test_missing_pricing_snapshot_bills_explained_zero(
    app: Any, db_session: AsyncSession, owner: dict[str, str], redis_client: Any
) -> None:
    # M11's "carries a NULL unit_usd_per_unit" condition: the pricing_snapshots ROW
    # exists (so recorder.py's non-token dispatch actually runs) but its price column
    # is NULL — this is the condition that reaches the EXISTING
    # `unit_price_missing_for_non_token_unit` warning branch (which only fires INSIDE
    # the `if pricing is not None:` guard). The OTHER M11 condition (the row entirely
    # absent) hits a different, earlier code path where `pricing is None` short-
    # circuits resolved_pricing_unit back to "per_token" before dispatch ever
    # considers "per_tool_call" — not the path this scenario's Then-clauses describe.
    await seed_mcp_tool_call_pricing(db_session, unit_usd_per_unit=None)

    observer = _build_observer(app, redis_client)
    await observer.record(
        call_id=uuid.uuid4(),
        tenant_id=uuid.UUID(owner["tenant_id"]),
        key_id=uuid.UUID(owner["key_id"]),
        server_host="mcp.acme.example",
        tool_name="search",
        status="success",
        latency_ms=100,
    )
    await _flush(app, redis_client)

    rows = await _usage_rows(db_session, owner["tenant_id"])
    assert len(rows) == 1
    row = rows[0]
    assert Decimal(str(row["cost_usd"])) == Decimal("0")
    assert row["pricing_unit"] == "per_tool_call"
    assert Decimal(str(row["quantity"])) == Decimal("1")
    assert row["tags"] == {"mcp_server": "mcp.acme.example", "mcp_tool": "search"}


# ===========================================================================
# Scenario: Concurrent tool calls across two tenants never cross-contaminate billing
# ===========================================================================


async def test_concurrent_calls_across_tenants_do_not_cross_contaminate(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
    owner_b: dict[str, str],
    redis_client: Any,
) -> None:
    await seed_mcp_tool_call_pricing(db_session)

    # Tenant A overrides markup to 50%; tenant B keeps the flat default (20%).
    resp = await client.put(
        f"/admin/rate-cards/{MCP_TOOL_CALL_MODEL_ID}",
        json={"markup_pct": "50"},
        headers=bearer(owner["jwt"]),
    )
    assert resp.status_code == 200, resp.text

    observer = _build_observer(app, redis_client)

    await asyncio.gather(
        observer.record(
            call_id=uuid.uuid4(),
            tenant_id=uuid.UUID(owner["tenant_id"]),
            key_id=uuid.UUID(owner["key_id"]),
            server_host="mcp.acme.example",
            tool_name="search",
            status="success",
            latency_ms=100,
        ),
        observer.record(
            call_id=uuid.uuid4(),
            tenant_id=uuid.UUID(owner_b["tenant_id"]),
            key_id=uuid.UUID(owner_b["key_id"]),
            server_host="mcp.acme.example",
            tool_name="search",
            status="success",
            latency_ms=100,
        ),
    )
    await _flush(app, redis_client)

    rows_a = await _usage_rows(db_session, owner["tenant_id"])
    rows_b = await _usage_rows(db_session, owner_b["tenant_id"])
    assert len(rows_a) == 1
    assert len(rows_b) == 1
    cost_a = Decimal("1") * Decimal("0.0025") * (Decimal("1") + Decimal("50") / Decimal("100"))
    cost_b = Decimal("1") * Decimal("0.0025") * (Decimal("1") + Decimal("20") / Decimal("100"))
    assert Decimal(str(rows_a[0]["cost_usd"])) == cost_a
    assert Decimal(str(rows_b[0]["cost_usd"])) == cost_b
    assert cost_a != cost_b, "tenant A's override must never leak into tenant B's cost"
