"""SQLAlchemy implementation of CatalogRepository."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.domain.entities import CatalogModel, MarkedUpModel
from gateway.catalog.domain.errors import CatalogEmptyError
from gateway.catalog.infrastructure.orm import ModelRow, PricingSnapshotRow
from gateway.core.ids import uuid7
from gateway.tenants.infrastructure.orm import TenantRow


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
        """Single joined query: active models x latest snapshot x tenant markup.

        No N+1 — uses a lateral/subquery approach.
        Raises CatalogEmptyError when zero active models exist.
        """
        # Subquery: latest snapshot per model_id
        snap_sub = (
            select(
                PricingSnapshotRow.model_id,
                PricingSnapshotRow.prompt_usd_per_token,
                PricingSnapshotRow.completion_usd_per_token,
                PricingSnapshotRow.cached_input_usd_per_token,
                PricingSnapshotRow.audio_prompt_usd_per_token,
                PricingSnapshotRow.audio_completion_usd_per_token,
                PricingSnapshotRow.audio_cached_usd_per_token,
            )
            .distinct(PricingSnapshotRow.model_id)
            .order_by(
                PricingSnapshotRow.model_id,
                PricingSnapshotRow.captured_at.desc(),
            )
            .subquery("latest_snap")
        )

        stmt = (
            select(
                ModelRow.id,
                ModelRow.name,
                ModelRow.context_length,
                ModelRow.input_modalities,
                snap_sub.c.prompt_usd_per_token,
                snap_sub.c.completion_usd_per_token,
                snap_sub.c.cached_input_usd_per_token,
                snap_sub.c.audio_prompt_usd_per_token,
                snap_sub.c.audio_completion_usd_per_token,
                snap_sub.c.audio_cached_usd_per_token,
                TenantRow.markup_pct,
            )
            .join(snap_sub, snap_sub.c.model_id == ModelRow.id)
            .join(TenantRow, TenantRow.id == tenant_id)
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
                    # catalog-pricing-fields TASK.md §3: None-safe — no cache price today
                    # for the vast majority of models; never coerced to 0.
                    cached_input_per_token=(
                        float(row.cached_input_usd_per_token) * multiplier
                        if row.cached_input_usd_per_token is not None
                        else None
                    ),
                    # gpt-realtime-pricing-fields TASK.md §3: None-safe — no audio stream today
                    # for any model except gpt-realtime; never coerced to 0.
                    audio_prompt_per_token=(
                        float(row.audio_prompt_usd_per_token) * multiplier
                        if row.audio_prompt_usd_per_token is not None
                        else None
                    ),
                    audio_completion_per_token=(
                        float(row.audio_completion_usd_per_token) * multiplier
                        if row.audio_completion_usd_per_token is not None
                        else None
                    ),
                    audio_cached_per_token=(
                        float(row.audio_cached_usd_per_token) * multiplier
                        if row.audio_cached_usd_per_token is not None
                        else None
                    ),
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
    ) -> dict[
        str,
        tuple[
            Decimal,
            Decimal,
            Decimal | None,
            Decimal | None,
            Decimal | None,
            Decimal | None,
        ],
    ]:
        """Bulk-fetch the most recent snapshot prices for a set of model IDs.

        catalog-pricing-fields TASK.md §3: the tuple grew a 3rd element
        (cached_input_usd_per_token, None when absent) so _price_changed can detect a
        cache-price-only change and append a new snapshot.

        gpt-realtime-pricing-fields TASK.md §3: the tuple grows 3 MORE elements
        (audio_prompt/audio_completion/audio_cached_usd_per_token, None when absent) for the
        same reason — an audio-price-only change must also append a new snapshot.
        """
        if not model_ids:
            return {}

        # Distinct on model_id ordered by captured_at DESC gives latest per model.
        snap_sub = (
            select(
                PricingSnapshotRow.model_id,
                PricingSnapshotRow.prompt_usd_per_token,
                PricingSnapshotRow.completion_usd_per_token,
                PricingSnapshotRow.cached_input_usd_per_token,
                PricingSnapshotRow.audio_prompt_usd_per_token,
                PricingSnapshotRow.audio_completion_usd_per_token,
                PricingSnapshotRow.audio_cached_usd_per_token,
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
                Decimal(str(row.cached_input_usd_per_token))
                if row.cached_input_usd_per_token is not None
                else None,
                Decimal(str(row.audio_prompt_usd_per_token))
                if row.audio_prompt_usd_per_token is not None
                else None,
                Decimal(str(row.audio_completion_usd_per_token))
                if row.audio_completion_usd_per_token is not None
                else None,
                Decimal(str(row.audio_cached_usd_per_token))
                if row.audio_cached_usd_per_token is not None
                else None,
            )
            for row in rows
        }

    async def _upsert_model(self, model: CatalogModel) -> None:
        """Insert or update (on conflict) the model row, setting active=true.

        minimax-catalog-seed TASK.md §3: now writes `provider` on BOTH the insert and the
        conflict-update, fixing the pre-existing bug where it silently fell back to the column
        server_default regardless of the in-memory CatalogModel's value. `input_modalities` is
        written on INSERT only — model-input-capabilities TASK.md §2 SC5 froze the invariant that
        sync must never clobber a seeded/admin-set input_modalities value on re-sync, so it is
        deliberately absent from the conflict-update `set_`.
        """
        stmt = (
            pg_insert(ModelRow)
            .values(
                id=model.id,
                name=model.name,
                context_length=model.context_length,
                active=True,
                modality=model.modality,
                provider=model.provider,
                input_modalities=model.input_modalities,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": model.name,
                    "context_length": model.context_length,
                    "active": True,
                    "modality": model.modality,
                    "provider": model.provider,
                },
            )
        )
        await self._session.execute(stmt)

    async def _insert_snapshot(self, model: CatalogModel) -> None:
        """Append a new immutable pricing snapshot row.

        UUID is generated EXPLICITLY here — never rely on column defaults pre-flush.

        catalog-pricing-fields TASK.md §3: also persists cached_input_usd_per_token (the
        pre-existing tiered-token-billing column) when the model carries one — NULL otherwise,
        preserving byte-identical behavior for every provider without a cache price.

        gpt-realtime-pricing-fields TASK.md §3: also persists the 3 audio_* columns (added by
        gpt-realtime-schema-migration) when the model carries them — NULL otherwise, same
        byte-identical-for-everyone-else discipline.
        """
        snap = PricingSnapshotRow(
            id=uuid7(),
            model_id=model.id,
            prompt_usd_per_token=model.prompt_usd_per_token,
            completion_usd_per_token=model.completion_usd_per_token,
            cached_input_usd_per_token=model.cached_input_usd_per_token,
            audio_prompt_usd_per_token=model.audio_prompt_usd_per_token,
            audio_completion_usd_per_token=model.audio_completion_usd_per_token,
            audio_cached_usd_per_token=model.audio_cached_usd_per_token,
        )
        self._session.add(snap)

    @staticmethod
    def _price_changed(
        prev: tuple[
            Decimal,
            Decimal,
            Decimal | None,
            Decimal | None,
            Decimal | None,
            Decimal | None,
        ]
        | None,
        model: CatalogModel,
    ) -> bool:
        """Return True if this model has no prior snapshot or any of its 6 prices differ.

        catalog-pricing-fields TASK.md §3: extended to a 3-way comparison (prompt, completion,
        cached_input) so a cache-price-only change still appends a new append-only snapshot row.

        gpt-realtime-pricing-fields TASK.md §3: extended to a 6-way comparison (+ audio_prompt,
        audio_completion, audio_cached) so an audio-price-only change also appends one.
        """
        if prev is None:
            return True
        (
            prev_prompt,
            prev_completion,
            prev_cached,
            prev_audio_prompt,
            prev_audio_completion,
            prev_audio_cached,
        ) = prev
        model_cached = (
            Decimal(str(model.cached_input_usd_per_token))
            if model.cached_input_usd_per_token is not None
            else None
        )
        model_audio_prompt = (
            Decimal(str(model.audio_prompt_usd_per_token))
            if model.audio_prompt_usd_per_token is not None
            else None
        )
        model_audio_completion = (
            Decimal(str(model.audio_completion_usd_per_token))
            if model.audio_completion_usd_per_token is not None
            else None
        )
        model_audio_cached = (
            Decimal(str(model.audio_cached_usd_per_token))
            if model.audio_cached_usd_per_token is not None
            else None
        )
        return (
            prev_prompt != Decimal(str(model.prompt_usd_per_token))
            or prev_completion != Decimal(str(model.completion_usd_per_token))
            or prev_cached != model_cached
            or prev_audio_prompt != model_audio_prompt
            or prev_audio_completion != model_audio_completion
            or prev_audio_cached != model_audio_cached
        )
