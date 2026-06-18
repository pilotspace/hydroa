"""RED suite for stt-nonfinite-passthrough (TASK.md §4, scenarios NF1–NF6).

The STT handler echoes the upstream body via Starlette's JSONResponse, whose render()
hard-codes allow_nan=False — so ANY non-finite float (inf/-inf/nan) anywhere in the body
raises ValueError → 500 on RESPONSE serialization (independent of billing). This task adds
a pure `sanitize_non_finite(obj) -> (sanitized, count)` helper, applied in
TranscriptionUseCase.execute before the return, that replaces every non-finite float with
null and WARNs `stt_nonfinite_sanitized` {model, count} once when it bites.

RED at write time: the helper does not exist and execute does not sanitize, so a non-finite
body 500s/raises (NF1/NF2/NF4/NF6) and the NF5 import fails; NF3 is the all-finite guard.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.audio_endpoints.conftest import (
    STT_MODEL_ID,
    inject_fake_openai_audio_provider,
    seed_stt_model,
)

from .conftest import TRANSCRIPTIONS_PATH, auth_key, multipart_files

# pytest asyncio_mode=auto: `async def test_*` runs without a marker; NF5 stays sync.

NAN = float("nan")
INF = float("inf")
NINF = float("-inf")


async def _post(client: Any, api_key_info: dict[str, str]) -> Any:
    return await client.post(
        TRANSCRIPTIONS_PATH,
        files=multipart_files(),
        data={"model": STT_MODEL_ID},
        headers=auth_key(api_key_info["key"]),
    )


# ---------------------------------------------------------------------------
# NF1 — a nan top-level duration is sanitized to null, 200, WARNed
# ---------------------------------------------------------------------------


async def test_nf1_nan_duration_sanitized(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_stt_model(db_session)
    fake = inject_fake_openai_audio_provider(app)
    fake.set_multipart_response(200, {"text": "hi", "duration": NAN})

    with caplog.at_level("WARNING"):
        resp = await _post(client, api_key_info)

    assert resp.status_code == 200, f"non-finite body must not 500, got {resp.status_code}"
    body = resp.json()
    assert body["duration"] is None, f"nan duration must sanitize to null, got {body.get('duration')!r}"
    assert body["text"] == "hi"
    capped = [r for r in caplog.records if r.msg == "stt_nonfinite_sanitized"]
    assert len(capped) == 1, f"expected one stt_nonfinite_sanitized WARN, got {len(capped)}"
    assert capped[0].count == 1
    assert capped[0].model == STT_MODEL_ID


# ---------------------------------------------------------------------------
# NF2 — a non-finite nested in segments[] is sanitized (full-tree)
# ---------------------------------------------------------------------------


async def test_nf2_nested_segments_sanitized(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_stt_model(db_session)
    fake = inject_fake_openai_audio_provider(app)
    fake.set_multipart_response(
        200, {"text": "hi", "segments": [{"id": 0, "avg_logprob": NINF}]}
    )

    resp = await _post(client, api_key_info)

    assert resp.status_code == 200
    seg = resp.json()["segments"][0]
    assert seg["avg_logprob"] is None, f"nested -inf must sanitize, got {seg.get('avg_logprob')!r}"
    assert seg["id"] == 0


# ---------------------------------------------------------------------------
# NF3 — an all-finite body is byte-identical (no sanitize, no WARN)
# ---------------------------------------------------------------------------


async def test_nf3_all_finite_byte_identical(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_stt_model(db_session)
    fake = inject_fake_openai_audio_provider(app)
    finite_body = {"text": "hi", "duration": 12.5, "segments": [{"avg_logprob": -0.3}]}
    fake.set_multipart_response(200, finite_body)

    with caplog.at_level("WARNING"):
        resp = await _post(client, api_key_info)

    assert resp.status_code == 200
    assert resp.json() == finite_body, "an all-finite body must echo verbatim"
    assert "stt_nonfinite_sanitized" not in caplog.text


# ---------------------------------------------------------------------------
# NF4 — every non-finite form (nan, inf, -inf) maps to null; WARN count == 3
# ---------------------------------------------------------------------------


async def test_nf4_all_nonfinite_forms_to_null(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_stt_model(db_session)
    fake = inject_fake_openai_audio_provider(app)
    fake.set_multipart_response(
        200, {"a": NAN, "b": INF, "c": NINF, "text": "hi"}
    )

    with caplog.at_level("WARNING"):
        resp = await _post(client, api_key_info)

    assert resp.status_code == 200
    body = resp.json()
    assert body["a"] is None and body["b"] is None and body["c"] is None
    assert body["text"] == "hi"
    capped = [r for r in caplog.records if r.msg == "stt_nonfinite_sanitized"]
    assert len(capped) == 1
    assert capped[0].count == 3, f"three non-finite values → count 3, got {capped[0].count}"


# ---------------------------------------------------------------------------
# NF5 — sanitize_non_finite is a pure total transform (unit, no infra)
# ---------------------------------------------------------------------------


def test_nf5_sanitize_non_finite_pure_unit() -> None:
    # Imported INSIDE the test so the RED collection of the HTTP tests is not blocked
    # by the missing module (the honest red for NF5 is this ImportError).
    from gateway.proxy.application.json_sanitize import sanitize_non_finite

    src: dict[str, Any] = {
        "i": 1,
        "f": 2.5,
        "nan": NAN,
        "true": True,
        "false": False,
        "s": "x",
        "none": None,
        "list": [0.0, -0.0, INF, {"deep": NINF, "k": 3}],
    }
    out, count = sanitize_non_finite(src)

    assert count == 3, f"nan + inf + -inf → 3, got {count}"
    # non-finite floats → None
    assert out["nan"] is None
    assert out["list"][2] is None  # inf
    assert out["list"][3]["deep"] is None  # -inf nested
    # everything else unchanged (ints, bools NOT treated as float, strings, None, finite floats)
    assert out["i"] == 1 and out["f"] == 2.5 and out["s"] == "x" and out["none"] is None
    assert out["true"] is True and out["false"] is False
    assert out["list"][0] == 0.0 and out["list"][1] == -0.0 and out["list"][3]["k"] == 3
    # purity: the input is not mutated, NEW containers are returned at every level
    assert out is not src
    assert out["list"] is not src["list"]  # nested list rebuilt, not aliased
    assert out["list"][3] is not src["list"][3]  # nested dict rebuilt, not aliased
    assert src["list"][2] == INF and math.isinf(src["list"][3]["deep"])
    # total on scalar leaves — never raises
    assert sanitize_non_finite(NAN) == (None, 1)
    assert sanitize_non_finite("plain") == ("plain", 0)
    assert sanitize_non_finite(42) == (42, 0)
    assert sanitize_non_finite(True) == (True, 0)


# ---------------------------------------------------------------------------
# NF6 — the status is preserved and the non-nan body still echoes
# ---------------------------------------------------------------------------


async def test_nf6_status_preserved_body_echoed(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_stt_model(db_session)
    fake = inject_fake_openai_audio_provider(app)
    fake.set_multipart_response(
        200, {"text": "hello world", "language": "en", "duration": NAN}
    )

    resp = await _post(client, api_key_info)

    assert resp.status_code == 200, "sanitizer must never change the upstream status"
    body = resp.json()
    assert body["text"] == "hello world" and body["language"] == "en"
    assert body["duration"] is None
