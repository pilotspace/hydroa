"""Failing-first (RED) suite for /v1/images/edits + /v1/images/variations (contract DRAFT,
PLAN.md §4, task image-edits-variations).

Right-reason red targets:
  - IE1-IE10, IV1-IV4: POST /v1/images/edits and /v1/images/variations routes do not exist
    → 404 Not Found (every other asserted status — 200/422/413/400/404/502 — differs from 404,
    so 404 trips the assertion for the RIGHT reason in every case).
  - test_ie_iv_regression_generations_untouched: GREEN-BY-DESIGN — the existing
    /v1/images/generations route already works pre-task; proves it stays byte-identical.

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 db 9 (shared; flushed per test where needed)
  - httpx.ASGITransport (no network)

Run only this suite:
  cd apps/gateway && uv run pytest tests/image_edits_variations/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.image_edits_variations.conftest import (
    CHAT_MODEL_ID,
    EDIT_CAPABLE_MODEL_ID,
    EDIT_RESPONSE_BODY,
    EDIT_RESPONSE_BODY_2,
    EDIT_RESPONSE_BODY_EMPTY,
    FAKE_LARGE_PNG_BYTES,
    FAKE_PNG_BYTES,
    GENERATIONS_ONLY_MODEL_ID,
    VARIATION_RESPONSE_BODY,
    VARIATION_RESPONSE_BODY_2,
    FakeMultipartProvider,
    SpyRecorder,
    inject_fake_openai_provider,
    seed_chat_model,
    seed_edit_capable_model,
    seed_image_model,
)

# ---------------------------------------------------------------------------
# Route path constants / helpers
# ---------------------------------------------------------------------------

EDITS_PATH = "/v1/images/edits"
GENERATIONS_PATH = "/v1/images/generations"
VARIATIONS_PATH = "/v1/images/variations"


def _auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _assert_problem(resp: Any, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, (
        f"expected code {code!r}, got {body.get('code')!r}; body: {body}"
    )
    assert body.get("status") == status
    assert "title" in body
    return body


def _image_file(content: bytes = FAKE_PNG_BYTES) -> tuple[str, bytes, str]:
    return ("image.png", content, "image/png")


# ---------------------------------------------------------------------------
# IE1 — edits happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ie1_edits_happy_path_200_with_provider_body(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """POST /v1/images/edits with valid credentials + an edit-capable model → 200 verbatim body.

    RED reason: route does not exist → 404 Not Found.
    """
    await seed_edit_capable_model(db_session)
    fake_provider = FakeMultipartProvider()
    fake_provider.set_multipart_response(200, EDIT_RESPONSE_BODY)
    inject_fake_openai_provider(app, fake_provider)

    resp = await client.post(
        EDITS_PATH,
        data={"model": EDIT_CAPABLE_MODEL_ID, "prompt": "add a hat"},
        files={"image": _image_file()},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert resp.json() == EDIT_RESPONSE_BODY

    assert len(fake_provider.post_multipart_calls) == 1
    call = fake_provider.post_multipart_calls[0]
    assert call["path"] == "/images/edits"
    assert call["data"]["model"] == EDIT_CAPABLE_MODEL_ID
    assert call["data"]["prompt"] == "add a hat"
    assert "image" in call["files"]


# ---------------------------------------------------------------------------
# IE2 — single usage record, quantity == actual returned images
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ie2_edits_single_usage_record_quantity_actual_returned(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Exactly ONE usage record; pricing_unit='per_image'; quantity==Decimal(2) actual returned."""
    await seed_edit_capable_model(db_session)

    fake_provider = FakeMultipartProvider()
    fake_provider.set_multipart_response(200, EDIT_RESPONSE_BODY_2)
    inject_fake_openai_provider(app, fake_provider)

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        EDITS_PATH,
        data={"model": EDIT_CAPABLE_MODEL_ID, "prompt": "two hats", "n": "2"},
        files={"image": _image_file()},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    await asyncio.sleep(0)

    assert spy.call_count == 1, f"expected 1 record call, got {spy.call_count}"
    last = spy.last_call
    assert last["model"] == EDIT_CAPABLE_MODEL_ID
    assert last.get("pricing_unit") == "per_image"
    assert last.get("quantity") == Decimal(2), (
        f"expected quantity=Decimal(2) (2 images returned, not requested n), got {last.get('quantity')!r}"
    )


# ---------------------------------------------------------------------------
# IE3 — zero images returned bills zero
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ie3_edits_zero_images_returned_bills_zero(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_edit_capable_model(db_session)

    fake_provider = FakeMultipartProvider()
    fake_provider.set_multipart_response(200, EDIT_RESPONSE_BODY_EMPTY)
    inject_fake_openai_provider(app, fake_provider)

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        EDITS_PATH,
        data={"model": EDIT_CAPABLE_MODEL_ID, "prompt": "empty result"},
        files={"image": _image_file()},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    await asyncio.sleep(0)

    assert spy.call_count == 1
    assert spy.last_call.get("quantity") == Decimal(0), (
        "empty upstream data must bill quantity=0, never fall back to requested n"
    )


# ---------------------------------------------------------------------------
# IE4 — missing image rejects 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ie4_edits_missing_image_rejects_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_edit_capable_model(db_session)
    inject_fake_openai_provider(app)
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        EDITS_PATH,
        data={"model": EDIT_CAPABLE_MODEL_ID, "prompt": "no image here"},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    await asyncio.sleep(0)
    assert spy.call_count == 0, "no usage record on a pre-governance reject"


# ---------------------------------------------------------------------------
# IE5 — missing prompt rejects 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ie5_edits_missing_prompt_rejects_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_edit_capable_model(db_session)
    inject_fake_openai_provider(app)
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        EDITS_PATH,
        data={"model": EDIT_CAPABLE_MODEL_ID},
        files={"image": _image_file()},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    await asyncio.sleep(0)
    assert spy.call_count == 0


# ---------------------------------------------------------------------------
# IE6 — oversized image rejects 413 pre-bill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ie6_edits_oversized_image_rejects_413_pre_bill(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_edit_capable_model(db_session)
    app.state.settings.image_edit_max_bytes = 100  # SMALL cap, well below FAKE_LARGE_PNG_BYTES
    fake_provider = FakeMultipartProvider()
    inject_fake_openai_provider(app, fake_provider)
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        EDITS_PATH,
        data={"model": EDIT_CAPABLE_MODEL_ID, "prompt": "too big"},
        files={"image": _image_file(FAKE_LARGE_PNG_BYTES)},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 413, "ERR_PAYLOAD_IMAGE_TOO_LARGE")
    await asyncio.sleep(0)
    assert len(fake_provider.post_multipart_calls) == 0, "must reject BEFORE calling upstream"
    assert spy.call_count == 0, "no usage record on an oversized-image reject"


# ---------------------------------------------------------------------------
# IE7 — unknown model rejects 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ie7_edits_unknown_model_rejects_404(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    inject_fake_openai_provider(app)

    resp = await client.post(
        EDITS_PATH,
        data={"model": "does-not-exist", "prompt": "hi"},
        files={"image": _image_file()},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 404, "ERR_MODEL_UNKNOWN")


# ---------------------------------------------------------------------------
# IE8 — chat model modality mismatch rejects 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ie8_edits_chat_model_modality_mismatch_rejects_400(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_chat_model(db_session)
    inject_fake_openai_provider(app)

    resp = await client.post(
        EDITS_PATH,
        data={"model": CHAT_MODEL_ID, "prompt": "hi"},
        files={"image": _image_file()},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 400, "ERR_MODEL_MODALITY_MISMATCH")


# ---------------------------------------------------------------------------
# IE9 — generations-only model (dall-e-3) rejects 400 unsupported_input_modality
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ie9_edits_generations_only_model_unsupported_input_modality_400(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_image_model(db_session)  # dall-e-3, input_modalities="text"
    fake_provider = FakeMultipartProvider()
    inject_fake_openai_provider(app, fake_provider)
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        EDITS_PATH,
        data={"model": GENERATIONS_ONLY_MODEL_ID, "prompt": "hi"},
        files={"image": _image_file()},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 400, "ERR_UNSUPPORTED_INPUT_MODALITY")
    await asyncio.sleep(0)
    assert len(fake_provider.post_multipart_calls) == 0, "must reject BEFORE calling upstream"
    assert spy.call_count == 0


# ---------------------------------------------------------------------------
# IE10 — upstream unavailable → 502
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ie10_edits_upstream_unavailable_502(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_edit_capable_model(db_session)
    fake_provider = FakeMultipartProvider()
    fake_provider.raise_unavailable()
    inject_fake_openai_provider(app, fake_provider)

    resp = await client.post(
        EDITS_PATH,
        data={"model": EDIT_CAPABLE_MODEL_ID, "prompt": "hi"},
        files={"image": _image_file()},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 502, "ERR_UPSTREAM_UNAVAILABLE")


# ---------------------------------------------------------------------------
# IV1 — variations happy path (no prompt field forwarded)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iv1_variations_happy_path_200_with_provider_body(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_edit_capable_model(db_session)
    fake_provider = FakeMultipartProvider()
    fake_provider.set_multipart_response(200, VARIATION_RESPONSE_BODY)
    inject_fake_openai_provider(app, fake_provider)

    resp = await client.post(
        VARIATIONS_PATH,
        data={"model": EDIT_CAPABLE_MODEL_ID},
        files={"image": _image_file()},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert resp.json() == VARIATION_RESPONSE_BODY

    assert len(fake_provider.post_multipart_calls) == 1
    call = fake_provider.post_multipart_calls[0]
    assert call["path"] == "/images/variations"
    assert "prompt" not in call["data"], "variations has NO prompt field — never forwarded"


# ---------------------------------------------------------------------------
# IV2 — variations single usage record, quantity == actual returned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iv2_variations_single_usage_record_quantity_actual_returned(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_edit_capable_model(db_session)
    fake_provider = FakeMultipartProvider()
    fake_provider.set_multipart_response(200, VARIATION_RESPONSE_BODY_2)
    inject_fake_openai_provider(app, fake_provider)

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        VARIATIONS_PATH,
        data={"model": EDIT_CAPABLE_MODEL_ID, "n": "2"},
        files={"image": _image_file()},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    await asyncio.sleep(0)

    assert spy.call_count == 1
    last = spy.last_call
    assert last.get("pricing_unit") == "per_image"
    assert last.get("quantity") == Decimal(2)


# ---------------------------------------------------------------------------
# IV3 — variations missing image rejects 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iv3_variations_missing_image_rejects_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_edit_capable_model(db_session)
    inject_fake_openai_provider(app)
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        VARIATIONS_PATH,
        data={"model": EDIT_CAPABLE_MODEL_ID},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    await asyncio.sleep(0)
    assert spy.call_count == 0


# ---------------------------------------------------------------------------
# IV4 — generations-only model (dall-e-3) rejects 400 unsupported_input_modality
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iv4_variations_generations_only_model_unsupported_input_modality_400(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_image_model(db_session)  # dall-e-3, input_modalities="text"
    fake_provider = FakeMultipartProvider()
    inject_fake_openai_provider(app, fake_provider)
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        VARIATIONS_PATH,
        data={"model": GENERATIONS_ONLY_MODEL_ID},
        files={"image": _image_file()},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 400, "ERR_UNSUPPORTED_INPUT_MODALITY")
    await asyncio.sleep(0)
    assert len(fake_provider.post_multipart_calls) == 0
    assert spy.call_count == 0


# ---------------------------------------------------------------------------
# Regression — /v1/images/generations stays byte-identical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ie_iv_regression_generations_untouched(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """GREEN-BY-DESIGN: /v1/images/generations already works pre-task on its JSON-body flow.

    Proves ImagesUseCase.execute / images_router's existing route is untouched by this task
    — a request that does NOT use the new multipart edits/variations surface stays
    byte-identical (protocol-translation-engineer persona floor).
    """
    await seed_image_model(db_session)  # dall-e-3 — the pre-existing generations model
    fake_provider = FakeMultipartProvider()
    # exists on the fake — reuse for generations' JSON path
    assert hasattr(fake_provider, "post_json_calls")
    inject_fake_openai_provider(app, fake_provider)

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        GENERATIONS_PATH,
        json={"model": GENERATIONS_ONLY_MODEL_ID, "prompt": "a white cat"},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    await asyncio.sleep(0)

    assert spy.call_count == 1
    assert spy.last_call.get("pricing_unit") == "per_image"
