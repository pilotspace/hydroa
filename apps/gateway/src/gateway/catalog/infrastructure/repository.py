"""SQLAlchemy implementation of CatalogRepository."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.domain.entities import CatalogModel, MarkedUpModel
from gateway.catalog.domain.errors import CatalogEmptyError
from gateway.catalog.infrastructure.orm import ModelRow, PricingSnapshotRow
from gateway.core.ids import uuid7
from gateway.tenants.infrastructure.orm import TenantRow
from gateway.tenants.infrastructure.rate_card_orm import TenantRateCardEntry


class SqlAlchemyCatalogRepository:
    """CatalogRepository backed by PostgreSQL via SQLAlchemy async.

    Safety rule (§5): sync_catalog executes all writes inside ONE transaction.
    pricing_snapshots rows are INSERT-only — never UPDATEd or DELETEd.
    uuid7 IDs are generated EXPLICITLY at row construction before flush.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def sync_catalog(
        self,
        models: list[CatalogModel],
        *,
        embedding_models: list[CatalogModel] | None = None,
    ) -> int:
        """Upsert model rows, append snapshots for price changes, deactivate absent models.

        All writes happen in ONE transaction — partial failure leaves no rows.
        Returns count of models processed from source (active models in this sync run).

        embedding_models (openrouter-embeddings-routing TASK.md §3):
          NOT None -> upsert those rows too and include their ids in the blanket
            deactivation sweep (today's original semantics, now covering both
            modalities — a genuinely-retired embedding model IS deactivated).
          None -> the embeddings source was unavailable this cycle; deactivate
            ONLY modality="chat" rows absent from `models` — modality="embedding"
            rows are left completely untouched (neither upserted nor deactivated).
        """
        async with self._session.begin():
            all_models = models if embedding_models is None else [*models, *embedding_models]
            incoming_ids = {m.id for m in models}

            # 1. Fetch latest snapshot prices for all known models in one query.
            latest_prices = await self._fetch_latest_prices([m.id for m in all_models])

            # 2. Upsert model rows and append snapshots as needed.
            for model in all_models:
                await self._upsert_model(model)
                prev = latest_prices.get(model.id)
                if self._price_changed(prev, model):
                    await self._insert_snapshot(model)

            # 3. Deactivate models absent from the upstream response.
            if embedding_models is not None:
                incoming_ids |= {m.id for m in embedding_models}
                if incoming_ids:
                    await self._session.execute(
                        update(ModelRow)
                        .where(ModelRow.id.notin_(incoming_ids))
                        .values(active=False)
                    )
                else:
                    # All models absent — deactivate everything.
                    await self._session.execute(update(ModelRow).values(active=False))
            else:
                # Embeddings fetch unavailable this cycle — deactivate ONLY chat
                # rows absent from `models`; embedding-modality rows untouched.
                await self._session.execute(
                    update(ModelRow)
                    .where(ModelRow.id.notin_(incoming_ids), ModelRow.modality == "chat")
                    .values(active=False)
                )

        return len(all_models)

    async def list_active_models_with_markup(self, tenant_id: uuid.UUID) -> list[MarkedUpModel]:
        """Single joined query: active models x latest snapshot x effective markup.

        No N+1 — uses a lateral/subquery approach.
        Raises CatalogEmptyError when zero active models exist.

        tiered-rate-cards TASK.md §3: the effective per-row multiplier is
        COALESCE(tenant_rate_card_entries.markup_pct, tenants.markup_pct) — the
        bulk-join form of the SAME resolve rule recorder billing and
        cost_recovery use (gateway.usage.application.rate_card_resolver), so
        catalog display never drifts from what a request actually bills.
        """
        # Subquery: latest snapshot per model_id
        snap_sub = (
            select(
                PricingSnapshotRow.model_id,
                PricingSnapshotRow.prompt_usd_per_token,
                PricingSnapshotRow.completion_usd_per_token,
            )
            .distinct(PricingSnapshotRow.model_id)
            .order_by(
                PricingSnapshotRow.model_id,
                PricingSnapshotRow.captured_at.desc(),
            )
            .subquery("latest_snap")
        )

        effective_markup_pct = func.coalesce(
            TenantRateCardEntry.markup_pct, TenantRow.markup_pct
        ).label("markup_pct")

        stmt = (
            select(
                ModelRow.id,
                ModelRow.name,
                ModelRow.context_length,
                ModelRow.input_modalities,
                snap_sub.c.prompt_usd_per_token,
                snap_sub.c.completion_usd_per_token,
                effective_markup_pct,
            )
            .join(snap_sub, snap_sub.c.model_id == ModelRow.id)
            .join(TenantRow, TenantRow.id == tenant_id)
            .outerjoin(
                TenantRateCardEntry,
                (TenantRateCardEntry.tenant_id == tenant_id)
                & (TenantRateCardEntry.model_id == ModelRow.id),
            )
            .where(ModelRow.active.is_(True))
        )

        rows = (await self._session.execute(stmt)).all()

        if not rows:
            raise CatalogEmptyError("No active models in catalog")

        result: list[MarkedUpModel] = []
        for row in rows:
            multiplier = float(Decimal("1") + row.markup_pct / Decimal("100"))
            result.append(
                MarkedUpModel(
                    id=row.id,
                    name=row.name,
                    context_length=row.context_length,
                    prompt_per_token=float(row.prompt_usd_per_token) * multiplier,
                    completion_per_token=float(row.completion_usd_per_token) * multiplier,
                    # capabilities-admin-surface TASK.md §3: carry raw CSV through;
                    # endpoints decide how to surface it (lean public vs. admin).
                    input_modalities=row.input_modalities,
                )
            )
        return result

    async def get_latest_snapshot_prices(self, model_id: str) -> tuple[Decimal, Decimal] | None:
        """Return (prompt, completion) from most-recent snapshot, or None."""
        stmt = (
            select(
                PricingSnapshotRow.prompt_usd_per_token,
                PricingSnapshotRow.completion_usd_per_token,
            )
            .where(PricingSnapshotRow.model_id == model_id)
            .order_by(PricingSnapshotRow.captured_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        return (Decimal(str(row.prompt_usd_per_token)), Decimal(str(row.completion_usd_per_token)))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fetch_latest_prices(
        self, model_ids: list[str]
    ) -> dict[str, tuple[Decimal, Decimal]]:
        """Bulk-fetch the most recent snapshot prices for a set of model IDs."""
        if not model_ids:
            return {}

        # Distinct on model_id ordered by captured_at DESC gives latest per model.
        snap_sub = (
            select(
                PricingSnapshotRow.model_id,
                PricingSnapshotRow.prompt_usd_per_token,
                PricingSnapshotRow.completion_usd_per_token,
            )
            .distinct(PricingSnapshotRow.model_id)
            .where(PricingSnapshotRow.model_id.in_(model_ids))
            .order_by(
                PricingSnapshotRow.model_id,
                PricingSnapshotRow.captured_at.desc(),
            )
        )

        rows = (await self._session.execute(snap_sub)).all()
        return {
            row.model_id: (
                Decimal(str(row.prompt_usd_per_token)),
                Decimal(str(row.completion_usd_per_token)),
            )
            for row in rows
        }

    async def _upsert_model(self, model: CatalogModel) -> None:
        """Insert or update (on conflict) the model row, setting active=true.

        openrouter-embeddings-routing TASK.md §3: writes `modality` (sourced from
        the incoming CatalogModel, never hardcoded) on both the insert and the
        conflict-update. `provider`/`input_modalities` stay unwritten (out of
        scope; keep their column defaults).
        """
        stmt = (
            pg_insert(ModelRow)
            .values(
                id=model.id,
                name=model.name,
                context_length=model.context_length,
                active=True,
                modality=model.modality,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": model.name,
                    "context_length": model.context_length,
                    "active": True,
                    "modality": model.modality,
                },
            )
        )
        await self._session.execute(stmt)

    async def _insert_snapshot(self, model: CatalogModel) -> None:
        """Append a new immutable pricing snapshot row.

        UUID is generated EXPLICITLY here — never rely on column defaults pre-flush.
        """
        snap = PricingSnapshotRow(
            id=uuid7(),
            model_id=model.id,
            prompt_usd_per_token=model.prompt_usd_per_token,
            completion_usd_per_token=model.completion_usd_per_token,
        )
        self._session.add(snap)

    @staticmethod
    def _price_changed(prev: tuple[Decimal, Decimal] | None, model: CatalogModel) -> bool:
        """Return True if this model has no prior snapshot or prices differ."""
        if prev is None:
            return True
        prev_prompt, prev_completion = prev
        return prev_prompt != Decimal(
            str(model.prompt_usd_per_token)
        ) or prev_completion != Decimal(str(model.completion_usd_per_token))
