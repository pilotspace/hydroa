"""RED suite for stt-duration-cap (TASK.md §4, scenarios DCAP1–DCAP7).

The STT per_second billing path resolves a finite>0 but UNBOUNDED `duration_s` (from the
upstream-reported `duration` OR the server-derived header value) and bills
`quantity=Decimal(str(duration_s))`. A corrupt/lying header (or a lying upstream `duration`)
over-derives an absurd value → over-bill. This task clamps the resolved duration to a
configured maximum (`GATEWAY_STT_MAX_DURATION_SECONDS`, default 14400s = 4h) BEFORE billing,
WARNing `stt_duration_capped` on a clamp; byte-identical below the cap.

RED at write time: the knob `Settings.stt_max_duration_seconds`, the constructor param
`TranscriptionUseCase(max_duration_seconds=…)`, and the clamp block do not exist yet —
DCAP1/2/4/5/6/7 fail (or error on the missing knob/attr); DCAP3 is the byte-identical guard.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

# Harness reused from the audio_endpoints suite (plain module-level objects).
from tests.audio_endpoints.conftest import (
    FAKE_AUDIO_BYTES,
    STT_MODEL_ID,
    STT_RESPONSE_NO_DURATION,
    SpyRecorder,
    inject_fake_openai_audio_provider,
    seed_stt_model,
)

from gateway.core.config import Settings
from gateway.proxy.application.audio_use_case import TranscriptionUseCase

from .conftest import TRANSCRIPTIONS_PATH, auth_key, make_wav_bytes, multipart_files

# pytest is configured asyncio_mode=auto (pyproject.toml): `async def test_*` runs on the
# loop without a marker; the sync unit tests (DCAP6/DCAP7) stay sync.


# ---------------------------------------------------------------------------
# DCAP1 — derived duration over the cap is clamped + WARNed
# ---------------------------------------------------------------------------


async def test_dcap1_derived_over_cap_clamped(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(200, STT_RESPONSE_NO_DURATION)  # no upstream duration
    app.state.settings.stt_max_duration_seconds = 2.0  # SMALL cap

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    with caplog.at_level("WARNING"):
        resp = await client.post(
            TRANSCRIPTIONS_PATH,
            files=multipart_files(content=make_wav_bytes(seconds=5.0)),  # derived 5.0 > cap 2.0
            data={"model": STT_MODEL_ID},
            headers=auth_key(api_key_info["key"]),
        )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert spy.call_count == 1, f"expected 1 record call, got {spy.call_count}"
    rec = spy.last_call
    assert rec.get("pricing_unit") == "per_second", f"unexpected pricing_unit: {rec}"
    assert rec.get("quantity") == Decimal("2.0"), (
        f"derived 5.0 must clamp to cap 2.0, got {rec.get('quantity')}"
    )
    # The WARN must carry the contracted {model, original, cap} payload (TASK.md §3),
    # not merely the event name — assert the record's extra fields, not just substring.
    capped = [r for r in caplog.records if r.msg == "stt_duration_capped"]
    assert len(capped) == 1, f"expected exactly one stt_duration_capped record, got {len(capped)}"
    assert capped[0].original == 5.0
    assert capped[0].cap == 2.0
    assert capped[0].model == STT_MODEL_ID


# ---------------------------------------------------------------------------
# DCAP2 — upstream-reported duration over the cap is clamped + WARNed
# ---------------------------------------------------------------------------


async def test_dcap2_upstream_over_cap_clamped(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    # Upstream verbose_json reports an absurd duration — it WINS, then clamps.
    fake_provider.set_multipart_response(200, {"text": "Hello world", "duration": 30.0})
    app.state.settings.stt_max_duration_seconds = 2.0

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    with caplog.at_level("WARNING"):
        resp = await client.post(
            TRANSCRIPTIONS_PATH,
            files=multipart_files(content=make_wav_bytes(seconds=1.0)),  # derive path NOT consulted
            data={"model": STT_MODEL_ID},
            headers=auth_key(api_key_info["key"]),
        )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert spy.call_count == 1
    rec = spy.last_call
    assert rec.get("quantity") == Decimal("2.0"), (
        f"upstream 30.0 must clamp to cap 2.0, got {rec.get('quantity')}"
    )
    # WARN payload proves the UPSTREAM value (30.0) is the clamped original — not the
    # derived 1.0 — so the upstream branch is genuinely what got bounded (TASK.md §3).
    capped = [r for r in caplog.records if r.msg == "stt_duration_capped"]
    assert len(capped) == 1, f"expected exactly one stt_duration_capped record, got {len(capped)}"
    assert capped[0].original == 30.0
    assert capped[0].cap == 2.0


# ---------------------------------------------------------------------------
# DCAP3 — a normal-length file is byte-identical (no clamp, no WARN)
# ---------------------------------------------------------------------------


async def test_dcap3_normal_unchanged(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(200, STT_RESPONSE_NO_DURATION)
    app.state.settings.stt_max_duration_seconds = 10.0  # cap well above the file

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    with caplog.at_level("WARNING"):
        resp = await client.post(
            TRANSCRIPTIONS_PATH,
            files=multipart_files(content=make_wav_bytes(seconds=3.0)),  # 3.0 < cap 10.0
            data={"model": STT_MODEL_ID},
            headers=auth_key(api_key_info["key"]),
        )

    assert resp.status_code == 200
    assert spy.call_count == 1
    rec = spy.last_call
    assert rec.get("quantity") == Decimal("3.0"), (
        f"a sub-cap duration must bill unchanged, got {rec.get('quantity')}"
    )
    assert "stt_duration_capped" not in caplog.text


# ---------------------------------------------------------------------------
# DCAP4 — the $0 unavailable path is untouched by the cap
# ---------------------------------------------------------------------------


async def test_dcap4_unavailable_zero_untouched(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(200, STT_RESPONSE_NO_DURATION)
    app.state.settings.stt_max_duration_seconds = 2.0

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    with caplog.at_level("WARNING"):
        resp = await client.post(
            TRANSCRIPTIONS_PATH,
            files=multipart_files(content=FAKE_AUDIO_BYTES),  # undecodable → 0.0
            data={"model": STT_MODEL_ID},
            headers=auth_key(api_key_info["key"]),
        )

    assert resp.status_code == 200
    assert spy.call_count == 1
    rec = spy.last_call
    assert rec.get("quantity") == Decimal("0"), (
        f"undecodable header stays $0, got {rec.get('quantity')}"
    )
    assert "stt_duration_unavailable" in caplog.text
    assert "stt_duration_capped" not in caplog.text


# ---------------------------------------------------------------------------
# DCAP5 — duration exactly at the cap is not clamped and not WARNed (boundary)
# ---------------------------------------------------------------------------


async def test_dcap5_boundary_equal_cap_not_clamped(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(200, STT_RESPONSE_NO_DURATION)
    app.state.settings.stt_max_duration_seconds = 3.0  # cap == file duration

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    with caplog.at_level("WARNING"):
        resp = await client.post(
            TRANSCRIPTIONS_PATH,
            files=multipart_files(content=make_wav_bytes(seconds=3.0)),  # derived 3.0 == cap 3.0
            data={"model": STT_MODEL_ID},
            headers=auth_key(api_key_info["key"]),
        )

    assert resp.status_code == 200
    assert spy.call_count == 1
    rec = spy.last_call
    assert rec.get("quantity") == Decimal("3.0"), (
        f"duration == cap must NOT clamp, got {rec.get('quantity')}"
    )
    assert "stt_duration_capped" not in caplog.text  # clamp bites only on D > C


# ---------------------------------------------------------------------------
# DCAP6 — the cap knob has a strictly-positive default and rejects non-positive
# ---------------------------------------------------------------------------


def test_dcap6_knob_default_and_validation() -> None:
    settings = Settings()
    assert settings.stt_max_duration_seconds == 14400.0, (
        f"default must be 14400s (4h), got {settings.stt_max_duration_seconds!r}"
    )
    with pytest.raises(ValidationError) as ei:
        Settings(stt_max_duration_seconds=0)
    assert "stt_max_duration_seconds" in str(ei.value)


# ---------------------------------------------------------------------------
# DCAP7 — no cap injected leaves billing unbounded (legacy/test-double parity)
# ---------------------------------------------------------------------------


def test_dcap7_no_cap_default_is_none() -> None:
    # The constructor's max_duration_seconds defaults to None ⇒ no clamp. Existing
    # TranscriptionUseCase test-doubles construct without it and must keep working.
    uc = TranscriptionUseCase(
        governance=cast(Any, object()),
        session=cast(Any, object()),
    )
    assert uc._max_duration_seconds is None
