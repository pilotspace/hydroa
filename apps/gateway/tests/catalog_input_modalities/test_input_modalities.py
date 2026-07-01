"""Red suite for model-input-capabilities — TASK.md §4 (contract FROZEN @ v1).

8 tests, one per scenario in TASK.md §2. All tests MUST fail (ImportError,
AttributeError, or assertion failure) before the Build phase because the
entities/errors/ORM/migration symbols do not exist yet.

SC1  — additive column: existing chat row gets input_modalities='text' via server_default
SC2  — migration data-patch: audio_stt rows get input_modalities='audio'
SC3  — default_input_modalities is a pure total function
SC4  — normalize_input_modalities is deterministic
SC5  — seed sets input_modalities explicitly; _upsert_model never clobbers it
SC6  — entity.input_modalities is readable; API response bodies byte-identical (no new field)
SC7  — unknown token -> InvalidInputModalityError code="invalid_input_modality"
SC8  — empty set -> EmptyInputModalitiesError code="empty_input_modalities"

Run only this suite (from apps/gateway/):
  uv run pytest tests/catalog_input_modalities/ -q --no-cov -p no:randomly
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ===========================================================================
# SC1 — Additive column, existing rows byte-identical
# ===========================================================================


async def test_sc1_additive_column_existing_chat_row_gets_text_default(
    db_session: AsyncSession,
) -> None:
    """A pre-existing chat row gets input_modalities='text' via the server_default.

    RED reason: ModelRow ORM class lacks the 'input_modalities' column.
    The mapped_column() declaration and the migration are both absent — any attempt
    to INSERT into the models table without that column declaration will raise
    sqlalchemy.exc.InvalidRequestError or the column is missing on the returned row.
    """
    from gateway.catalog.infrastructure.orm import ModelRow
    from sqlalchemy import inspect as sa_inspect

    # Verify the column is declared on the ORM class.
    mapper = sa_inspect(ModelRow)
    column_names = {c.key for c in mapper.columns}
    assert "input_modalities" in column_names, (
        f"ModelRow ORM must have 'input_modalities' column; got: {column_names}"
    )

    # Insert a row WITHOUT specifying input_modalities — must receive server_default 'text'.
    row = ModelRow(
        id="test-chat-sc1",
        name="Test Chat Model",
        context_length=4096,
        active=True,
        modality="chat",
        provider="openrouter",
        # input_modalities intentionally omitted — must default to 'text'
    )
    db_session.add(row)
    await db_session.flush()
    await db_session.refresh(row)

    assert row.input_modalities == "text", (
        f"Chat row must have input_modalities='text' by default; got {row.input_modalities!r}"
    )
    # Other columns must be unchanged.
    assert row.id == "test-chat-sc1"
    assert row.modality == "chat"
    assert row.provider == "openrouter"
    assert row.context_length == 4096


# ===========================================================================
# SC2 — Non-chat existing rows re-derived in the same migration
# ===========================================================================


async def test_sc2_migration_data_patch_audio_stt_to_audio(
    db_session: AsyncSession,
) -> None:
    """The migration UPDATE sets audio_stt rows to 'audio'; others stay 'text'.

    RED reason: ModelRow ORM lacks 'input_modalities' → INSERT fails before the
    UPDATE can be verified.

    We simulate the migration's one-line data patch:
      UPDATE models SET input_modalities = 'audio' WHERE modality = 'audio_stt';
    and verify that the {embedding, image, audio_tts} rows retain 'text' while
    audio_stt rows become 'audio'.
    """
    from gateway.catalog.infrastructure.orm import ModelRow
    from sqlalchemy import select

    # Insert one row per modality (all start with server_default 'text').
    cases: list[tuple[str, str, str]] = [
        ("emb-sc2", "embedding", "openai"),
        ("img-sc2", "image", "openai"),
        ("tts-sc2", "audio_tts", "openai"),
        ("stt-sc2", "audio_stt", "openai"),
    ]
    for m_id, modality, provider in cases:
        db_session.add(
            ModelRow(
                id=m_id,
                name=f"{modality} model",
                context_length=None,
                active=True,
                modality=modality,
                provider=provider,
            )
        )
    await db_session.flush()

    # Replicate the migration's data-patch UPDATE.
    await db_session.execute(
        text("UPDATE models SET input_modalities = 'audio' WHERE modality = 'audio_stt'")
    )
    await db_session.flush()

    # Expire the identity map so that the next SELECT goes to the DB (not a stale cache),
    # since the raw text UPDATE does not automatically invalidate ORM-tracked objects.
    # expire_all() is synchronous on AsyncSession (no I/O — just marks objects expired).
    db_session.expire_all()

    # Build a lookup: model_id -> original modality (for the invariant check below).
    case_modalities: dict[str, str] = {m_id: modality for m_id, modality, _ in cases}

    # Verify expected input_modalities after migration UPDATE.
    expected: dict[str, str] = {
        "emb-sc2": "text",
        "img-sc2": "text",
        "tts-sc2": "text",
        "stt-sc2": "audio",
    }
    for m_id, want in expected.items():
        result = await db_session.execute(select(ModelRow).where(ModelRow.id == m_id))
        found = result.scalar_one()
        assert found.input_modalities == want, (
            f"Row {m_id!r} (modality={found.modality!r}): expected input_modalities={want!r}, "
            f"got {found.input_modalities!r}"
        )
        # modality column must NOT change (the UPDATE only touches input_modalities).
        assert found.modality == case_modalities[m_id], (
            f"Row {m_id!r}: modality must not change; got {found.modality!r}"
        )


# ===========================================================================
# SC3 — Default derivation is a pure total function
# ===========================================================================


def test_sc3_default_input_modalities_pure_total_function() -> None:
    """default_input_modalities(modality) returns the correct frozenset for every value.

    RED reason: default_input_modalities does not exist → ImportError.
    """
    from gateway.catalog.domain.entities import default_input_modalities  # type: ignore[attr-defined]

    # Standard modalities.
    assert default_input_modalities("chat") == frozenset({"text"}), "chat -> {text}"
    assert default_input_modalities("embedding") == frozenset({"text"}), "embedding -> {text}"
    assert default_input_modalities("image") == frozenset({"text"}), "image -> {text}"
    assert default_input_modalities("audio_tts") == frozenset({"text"}), "audio_tts -> {text}"
    assert default_input_modalities("audio_stt") == frozenset({"audio"}), "audio_stt -> {audio}"

    # Unknown value must return {"text"} — safe fallback, not an error.
    assert default_input_modalities("unknown_future_modality") == frozenset({"text"}), (
        "unknown modality must fall back to {text}, not raise"
    )


# ===========================================================================
# SC4 — Normalization is deterministic
# ===========================================================================


def test_sc4_normalize_input_modalities_deterministic() -> None:
    """normalize_input_modalities dedupes, sorts canonically, and joins with comma.

    RED reason: normalize_input_modalities does not exist → ImportError.
    """
    from gateway.catalog.domain.entities import normalize_input_modalities  # type: ignore[attr-defined]

    # Canonical order is text < image < audio (from _INPUT_MODALITY_ORDER).
    result = normalize_input_modalities(["audio", "text", "text"])
    assert result == "text,audio", (
        f"Expected 'text,audio' (canonical order, deduped); got {result!r}"
    )

    # All three tokens in reverse order → canonical order.
    result_all = normalize_input_modalities(["audio", "image", "text"])
    assert result_all == "text,image,audio", (
        f"Expected 'text,image,audio'; got {result_all!r}"
    )

    # Single token → just that token, no comma.
    result_single = normalize_input_modalities(["text"])
    assert result_single == "text", f"Expected 'text'; got {result_single!r}"

    # Blanks are stripped before validation.
    result_blank = normalize_input_modalities(["  text  ", "audio"])
    assert result_blank == "text,audio", f"Expected 'text,audio'; got {result_blank!r}"


# ===========================================================================
# SC5 — Seed sets capabilities explicitly; sync never clobbers them
# ===========================================================================


async def test_sc5_seed_sets_sync_never_clobbers(
    db_session: AsyncSession,
) -> None:
    """Seed row with input_modalities='text,image'; sync upsert leaves it unchanged.

    RED reason: ModelRow ORM lacks 'input_modalities' → INSERT fails.

    Verifies the no-clobber contract: _upsert_model's written column set
    (id, name, context_length, active, modality) must NOT include
    input_modalities so that a seeded or admin-edited value survives every sync.
    """
    from gateway.catalog.domain.entities import CatalogModel
    from gateway.catalog.infrastructure.orm import ModelRow
    from gateway.catalog.infrastructure.repository import SqlAlchemyCatalogRepository

    # Seed: insert a row with explicit input_modalities='text,image'.
    seeded_row = ModelRow(
        id="gpt-4o-sc5",
        name="GPT-4o (seeded)",
        context_length=128_000,
        active=True,
        modality="chat",
        provider="openai",
        input_modalities="text,image",  # explicitly seeded
    )
    db_session.add(seeded_row)
    await db_session.flush()

    # Sync: simulate OpenRouter upsert with changed name + context_length.
    repo = SqlAlchemyCatalogRepository(db_session)
    sync_model = CatalogModel(
        id="gpt-4o-sc5",
        name="GPT-4o (synced update)",  # name changed
        context_length=256_000,  # context_length changed
        prompt_usd_per_token=5e-6,
        completion_usd_per_token=15e-6,
        modality="chat",
        provider="openrouter",
        # input_modalities NOT provided on CatalogModel — defaults to 'text'
        # but _upsert_model MUST NOT write it, so seeded value is preserved.
    )
    await repo._upsert_model(sync_model)
    await db_session.flush()

    # Refresh from DB to pick up the upsert.
    await db_session.refresh(seeded_row)

    # Name and context_length are updated by sync (they ARE in the upsert set_).
    assert seeded_row.name == "GPT-4o (synced update)", (
        f"name must be updated by sync; got {seeded_row.name!r}"
    )
    assert seeded_row.context_length == 256_000, (
        f"context_length must be updated by sync; got {seeded_row.context_length!r}"
    )

    # input_modalities must be PRESERVED (not clobbered by sync).
    assert seeded_row.input_modalities == "text,image", (
        f"input_modalities must not be clobbered by sync; got {seeded_row.input_modalities!r}"
    )


# ===========================================================================
# SC6 — Field readable from entity; no API surfacing yet (byte-identical bodies)
# ===========================================================================


def test_sc6_field_readable_from_entity_no_api_surfacing() -> None:
    """entity.input_modalities is accessible; ModelItem/AdminModelItem have no such field.

    RED reason: domain entities ModelRow and CatalogModel lack input_modalities → AttributeError.

    This test asserts two things:
    1. The domain entities HAVE the field (readable by the next tasks).
    2. The API schemas (ModelItem, AdminModelItem, PutModelRequest) do NOT have this field,
       preserving byte-identical response bodies for all three /models endpoints.
    """
    from gateway.catalog.domain.entities import CatalogModel, ModelRow  # type: ignore[attr-defined]

    # 1. Domain entity CatalogModel has input_modalities with default 'text'.
    cat_model = CatalogModel(
        id="m-sc6",
        name="Model SC6",
        context_length=None,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        # input_modalities omitted — must default to 'text'
    )
    assert hasattr(cat_model, "input_modalities"), (
        "CatalogModel must have 'input_modalities' field"
    )
    assert cat_model.input_modalities == "text", (  # type: ignore[attr-defined]
        f"CatalogModel.input_modalities default must be 'text'; got {cat_model.input_modalities!r}"  # type: ignore[attr-defined]
    )

    # 2. Domain entity ModelRow has input_modalities with default 'text'.
    model_row = ModelRow(
        id="m-sc6",
        name="Model SC6",
        context_length=None,
        active=True,
        # input_modalities omitted — must default to 'text'
    )
    assert hasattr(model_row, "input_modalities"), (
        "ModelRow domain entity must have 'input_modalities' field"
    )
    assert model_row.input_modalities == "text", (  # type: ignore[attr-defined]
        f"ModelRow.input_modalities default must be 'text'; got {model_row.input_modalities!r}"  # type: ignore[attr-defined]
    )

    # 3. API schemas: ModelItem (public /v1/models) MUST NOT surface input_modalities —
    #    the public OpenAI-compatible shape must stay byte-identical (task 1 contract).
    #    AdminModelItem and AdminCatalogModelItem DO surface it (capabilities-admin-surface
    #    task 2 is the "next task" referenced in the original comment; it is now done).
    #    PutModelRequest is a request body and must never carry input_modalities.
    from gateway.catalog.api.schemas import AdminModelItem, ModelItem, PutModelRequest

    assert "input_modalities" not in ModelItem.model_fields, (
        "ModelItem MUST NOT have 'input_modalities' field — public /v1/models shape unchanged"
    )
    assert "input_modalities" not in PutModelRequest.model_fields, (
        "PutModelRequest MUST NOT have 'input_modalities' field"
    )
    # AdminModelItem now carries input_modalities (added by capabilities-admin-surface task 2).
    assert "input_modalities" in AdminModelItem.model_fields, (
        "AdminModelItem MUST have 'input_modalities' after capabilities-admin-surface task 2"
    )


# ===========================================================================
# SC7 — Unknown input-modality token is rejected
# ===========================================================================


def test_sc7_unknown_token_raises_invalid_input_modality() -> None:
    """normalize_input_modalities(["pdf"]) raises InvalidInputModalityError.

    RED reason: normalize_input_modalities and InvalidInputModalityError absent → ImportError.

    Verifies the error carries code="invalid_input_modality".
    No model row is written because the error fires before any DB interaction.
    """
    from gateway.catalog.domain.entities import normalize_input_modalities  # type: ignore[attr-defined]
    from gateway.catalog.domain.errors import InvalidInputModalityError  # type: ignore[attr-defined]

    with pytest.raises(InvalidInputModalityError) as exc_info:
        normalize_input_modalities(["pdf"])

    error = exc_info.value
    assert error.code == "invalid_input_modality", (  # type: ignore[attr-defined]
        f"Expected error.code='invalid_input_modality'; got {error.code!r}"  # type: ignore[attr-defined]
    )


# ===========================================================================
# SC8 — Empty input-modality set is rejected
# ===========================================================================


def test_sc8_empty_set_raises_empty_input_modalities() -> None:
    """normalize_input_modalities([]) raises EmptyInputModalitiesError.

    RED reason: normalize_input_modalities and EmptyInputModalitiesError absent → ImportError.

    Verifies the error carries code="empty_input_modalities".
    No model row is written because the error fires before any DB interaction.
    """
    from gateway.catalog.domain.entities import normalize_input_modalities  # type: ignore[attr-defined]
    from gateway.catalog.domain.errors import EmptyInputModalitiesError  # type: ignore[attr-defined]

    with pytest.raises(EmptyInputModalitiesError) as exc_info:
        normalize_input_modalities([])

    error = exc_info.value
    assert error.code == "empty_input_modalities", (  # type: ignore[attr-defined]
        f"Expected error.code='empty_input_modalities'; got {error.code!r}"  # type: ignore[attr-defined]
    )

    # Also: all-blank values collapse to empty after strip → same error.
    with pytest.raises(EmptyInputModalitiesError):
        normalize_input_modalities(["", "   "])
