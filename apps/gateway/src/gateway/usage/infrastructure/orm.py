"""SQLAlchemy ORM row for the usage_records append-only ledger.

Schema contract (FROZEN @ v1 — TASK.md §3):
  usage_records (
    id                  uuid        PRIMARY KEY,         -- Redis stream event id
    tenant_id           uuid        NOT NULL,
    key_id              uuid        NOT NULL,
    model_id            text        NOT NULL,
    prompt_tokens       int         NOT NULL DEFAULT 0,
    completion_tokens   int         NOT NULL DEFAULT 0,
    cost_usd            numeric(14,8) NOT NULL DEFAULT 0,
    status              int         NOT NULL,
    pricing_snapshot_id uuid        NULL,
    raw                 jsonb       NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    team_id             uuid        NULL          -- team-attribution (team-attribution TASK.md §3)
  )

APPEND-ONLY: no UPDATE or DELETE ever issued against this table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class UsageRecordRow(Base):
    """Append-only usage ledger row.

    id = Redis stream event id (converted to UUID from the millisecond-timestamp
    format "1234567890123-0" by stripping the hyphen and padding to 128 bits).
    The exact conversion used by the flusher must remain consistent across
    restarts — see infrastructure/redis_stream.py stream_id_to_uuid().
    """

    __tablename__ = "usage_records"
    # Composite index for team-rollup queries: tenant_id first (all queries are
    # tenant-scoped), then team_id for efficient per-team aggregation.
    # Partial index for the periodic cost-recovery sweep (v30 t6.3): scans
    # client_disconnect rows carrying a generation id with no openrouter_recovered
    # sibling. Partial predicate keeps it tiny — only gen-id-bearing rows are indexed.
    __table_args__ = (
        Index("ix_usage_records_tenant_team", "tenant_id", "team_id"),
        Index(
            "ix_usage_records_gen_recovery",
            "provider_generation_id",
            "usage_source",
            postgresql_where=text("provider_generation_id IS NOT NULL"),
        ),
        # Retention sweep: age-based DELETE WHERE created_at < :cutoff needs this index
        # to avoid a full-table scan on large ledgers. Added in migration f2a4c6e8b0d3.
        Index("ix_usage_records_created_at", "created_at"),
        # Reverse correlation to request_logs via the JSONB extras request_id (added in
        # migration a55ddcebaac6, request-log-metering-fields). Expression index on the
        # raw->>'request_id' extraction — declared here for alembic autogenerate parity.
        Index("ix_usage_records_request_id", text("(raw ->> 'request_id')")),
        # cost-attribution-tags (TASK.md §3): GIN index on the tags JSONB column —
        # accelerates containment lookups (tags @> '{"k":"v"}', used by the
        # invoice-generation sibling task). Does NOT accelerate the cost-by-tag
        # breakdown's jsonb_each_text GROUP BY expansion (a tenant+window-scoped
        # table scan, same scale as get_spend/get_guardrail_analytics).
        Index("ix_usage_records_tags_gin", "tags", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    key_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False, server_default="0")
    status: Mapped[int] = mapped_column(Integer, nullable=False)
    pricing_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    raw: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    # team-attribution: nullable — no FK (append-only ledger; team deletion must not cascade)
    team_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    # pricing-units (pricing-units TASK.md §3): discriminator + billed quantity
    pricing_unit: Mapped[str] = mapped_column(Text, nullable=False, server_default="per_token")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    # tiered-token-billing (TASK.md §3): per-tier token counts, queryable on the row.
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # prompt-cache-passthrough (TASK.md §3): Anthropic cache-write token count for this row.
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # provider-cost-reconciliation (TASK.md §3): which basis billed the row + the raw
    # upstream-reported cost (pre-markup) on provider-basis rows; NULL on catalog rows.
    cost_basis: Mapped[str] = mapped_column(Text, nullable=False, server_default="catalog")
    provider_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    # stream-usage-completeness (TASK.md §3): provenance of the billed usage —
    # 'frame' (real terminal usage frame) | 'stream_fallback' (frame missing/partial).
    usage_source: Mapped[str] = mapped_column(Text, nullable=False, server_default="frame")
    # provider-generation-id-capture (v30 t6): the provider's SSE generation id on a
    # client-disconnect row — the lookup key for disconnect cost-recovery. NULL on every
    # other row. Additive + nullable; append-only preserved (no UPDATE/DELETE).
    provider_generation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # gpt-realtime-schema-migration (TASK.md §3): audio-stream token counts, independent of
    # the text-stream prompt/completion/cached_tokens columns above. Default 0 on every
    # pre-existing row (only GPT-Realtime rows will ever populate these).
    audio_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    audio_completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    audio_cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # cost-attribution-tags (TASK.md §3): client-supplied key/value request labels,
    # written ONCE at insert time via the existing Redis-stream write-behind path
    # (never mutated afterward — append-only). Absent X-Gateway-Tags header -> {}
    # (byte-identical to every pre-task row). Queryable via GET /admin/usage/cost-by-tag.
    tags: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
