"""RED suite for stt-duration-derivation (TASK.md §4, scenarios SD1–SD7).

Closes the silent-$0 STT leak: when a caller omits `verbose_json`, the upstream
body carries no `duration`, so flat `body.get("duration") or 0.0` bills $0 for a
real transcription. This task derives the duration server-side by decoding the
uploaded audio header (tinytag) and bills `per_second` on the true length.

Frozen contract (§3):
  - NEW gateway.proxy.application.audio_duration.derive_duration_seconds(bytes)
    -> float | None — decode duration via tinytag; NEVER raises; non-positive /
    non-finite / undecodable -> None.
  - TranscriptionUseCase: upstream positive `duration` WINS (byte-identical, AU2);
    else derive from the uploaded bytes; else 0.0 + WARN "stt_duration_unavailable".

RED reason at write time: gateway.proxy.application.audio_duration does not exist
yet, so the module-level import fails → every test errors on collection. That is
the honest red — nothing in this task is implemented.
"""

from __future__ import annotations

import asyncio
import math
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# Harness reused from the audio_endpoints suite (plain module-level objects).
from tests.audio_endpoints.conftest import (
    FAKE_AUDIO_BYTES,
    STT_MODEL_ID,
    STT_RESPONSE_BODY,
    STT_RESPONSE_NO_DURATION,
    SpyRecorder,
    inject_fake_openai_audio_provider,
    seed_stt_model,
)

# The unit under test — module does not exist yet → ImportError is the RED reason.
from gateway.proxy.application import audio_duration
from gateway.proxy.application.audio_duration import derive_duration_seconds

from .conftest import TRANSCRIPTIONS_PATH, auth_key, make_wav_bytes, multipart_files

# pytest is configured asyncio_mode=auto (pyproject.toml), so `async def test_*`
# functions run on the event loop without a marker; the sync unit tests (SD4/5/6)
# stay sync. (No file-level pytest.mark.asyncio — it would warn on the sync tests.)


# ---------------------------------------------------------------------------
# SD1 — no verbose_json (upstream has no duration) + decodable file → derived bill
# ---------------------------------------------------------------------------


async def test_sd1_no_verbose_decodable_file_bills_derived_duration(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Upstream omits `duration`; a valid 3s WAV is uploaded → quantity == 3.0.

    This is the leak this task closes: today this case bills $0.
    """
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(200, STT_RESPONSE_NO_DURATION)  # no duration

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=multipart_files(content=make_wav_bytes(seconds=3.0)),
        data={"model": STT_MODEL_ID},
        headers=auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert spy.call_count == 1, f"expected 1 record call, got {spy.call_count}"
    rec = spy.last_call
    assert rec.get("pricing_unit") == "per_second", f"unexpected pricing_unit: {rec}"
    assert rec.get("quantity") == Decimal("3.0"), (
        f"expected derived quantity Decimal('3.0'), got {rec.get('quantity')}"
    )


# ---------------------------------------------------------------------------
# SD2 — upstream duration present → it WINS (byte-identical with AU2)
# ---------------------------------------------------------------------------


async def test_sd2_verbose_json_upstream_duration_wins(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """When the upstream reports a positive duration (verbose_json), it is billed
    as-is and the file is NOT decoded — preserves the frozen AU2 behavior."""
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(200, STT_RESPONSE_BODY)  # duration=12.5

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        # decodable 3s WAV — the upstream's 12.5 must still win, proving no override
        files=multipart_files(content=make_wav_bytes(seconds=3.0)),
        data={"model": STT_MODEL_ID},
        headers=auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert spy.call_count == 1, f"expected 1 record call, got {spy.call_count}"
    rec = spy.last_call
    assert rec.get("pricing_unit") == "per_second"
    assert rec.get("quantity") == Decimal("12.5"), (
        f"upstream duration must win unchanged, got {rec.get('quantity')}"
    )


# ---------------------------------------------------------------------------
# SD3 — undecodable header → $0 fallback + WARN, still 200 (preserves AU2b)
# ---------------------------------------------------------------------------


async def test_sd3_undecodable_header_zero_fallback_with_warn(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Upstream omits duration AND the uploaded bytes do not decode → quantity 0,
    a `stt_duration_unavailable` warning is logged, and the request still returns
    200 (accuracy is never an availability gate)."""
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(200, STT_RESPONSE_NO_DURATION)  # no duration

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    with caplog.at_level("WARNING"):
        resp = await client.post(
            TRANSCRIPTIONS_PATH,
            files=multipart_files(content=FAKE_AUDIO_BYTES),  # truncated WAV → None
            data={"model": STT_MODEL_ID},
            headers=auth_key(api_key_info["key"]),
        )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert spy.call_count == 1, f"expected 1 record call, got {spy.call_count}"
    rec = spy.last_call
    assert rec.get("pricing_unit") == "per_second"
    assert rec.get("quantity") == Decimal("0"), (
        f"undecodable header must fall back to Decimal('0'), got {rec.get('quantity')}"
    )
    assert "stt_duration_unavailable" in caplog.text, (
        f"expected stt_duration_unavailable warning, caplog: {caplog.text!r}"
    )


# ---------------------------------------------------------------------------
# SD4 — derive_duration_seconds fail-safe table (pure unit, NEVER raises)
# ---------------------------------------------------------------------------


def test_sd4_derive_duration_seconds_failsafe_table() -> None:
    """Decodes a real header to its exact seconds; every malformed input → None,
    and none of them raise."""
    valid = derive_duration_seconds(make_wav_bytes(seconds=3.0))
    assert valid == 3.0, f"expected 3.0 for a 3s WAV, got {valid!r}"
    assert isinstance(valid, float)

    # Each bad input must return None (NOT raise).
    assert derive_duration_seconds(b"") is None, "empty bytes"
    assert derive_duration_seconds(b"not audio at all, just ascii noise") is None, "garbage"
    assert derive_duration_seconds(FAKE_AUDIO_BYTES) is None, "truncated WAV (no data chunk)"


# ---------------------------------------------------------------------------
# SD5 — non-positive / non-finite reported duration → None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, 0.0, -1, -3.5, math.nan, math.inf, -math.inf])
def test_sd5_non_positive_or_nonfinite_duration_is_none(
    bad: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when the decoder yields a value, only a finite strictly-positive
    duration is billable — 0 / negative / NaN / ±inf all collapse to None."""

    class _FakeTag:
        duration = bad

    class _FakeTinyTag:
        @staticmethod
        def get(**_kwargs: Any) -> _FakeTag:
            return _FakeTag()

    monkeypatch.setattr(audio_duration, "TinyTag", _FakeTinyTag)
    assert derive_duration_seconds(make_wav_bytes(seconds=3.0)) is None, (
        f"non-positive/non-finite duration {bad!r} must be None"
    )


# ---------------------------------------------------------------------------
# SD6 — tinytag is a declared, allow-listed dependency (supply-chain gate)
# ---------------------------------------------------------------------------


def test_sd6_tinytag_is_declared_and_allowlisted() -> None:
    """The decode dependency must be pinned in pyproject AND in the ADD
    dependency allowlist — no invented/unvetted package slips in."""
    gateway_root = Path(__file__).resolve().parents[2]  # apps/gateway
    repo_root = gateway_root.parents[1]  # repo root

    pyproject = tomllib.loads((gateway_root / "pyproject.toml").read_text())
    deps = pyproject.get("project", {}).get("dependencies", [])
    assert any(d.lower().startswith("tinytag") for d in deps), (
        f"tinytag must be in [project.dependencies], got {deps}"
    )

    allowlist = (repo_root / ".add" / "dependencies.allowlist").read_text().lower()
    assert "tinytag" in allowlist, "tinytag must be in .add/dependencies.allowlist"


# ---------------------------------------------------------------------------
# SD7 — single-bill invariant survives derivation (exactly one record)
# ---------------------------------------------------------------------------


async def test_sd7_single_bill_invariant_on_derivation(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """A derived-duration transcription still fires exactly ONE usage record
    (deriving must not double-bill or skip the bill)."""
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(200, STT_RESPONSE_NO_DURATION)

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=multipart_files(content=make_wav_bytes(seconds=5.0)),
        data={"model": STT_MODEL_ID},
        headers=auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert spy.call_count == 1, f"derivation must single-bill, got {spy.call_count}"
    assert spy.last_call.get("quantity") == Decimal("5.0"), (
        f"expected derived Decimal('5.0'), got {spy.last_call.get('quantity')}"
    )


# ---------------------------------------------------------------------------
# SD8 — non-finite upstream duration is NOT authoritative → falls through to derive
# ---------------------------------------------------------------------------


async def test_sd8_non_finite_upstream_duration_falls_through_to_derive(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """A misbehaving upstream reporting duration=inf must NOT bill Decimal('Infinity')
    into the LEDGER. inf is non-finite → not authoritative → the bill derives from the
    file instead (mirrors the decoder's isfinite guard). RED before the guard: the
    upstream branch admits inf (inf > 0) → quantity = Decimal('Infinity').

    Scope note: echoing inf in the HTTP body is *separately* non-JSON-serializable
    (Starlette renders with allow_nan=False). v27 deferred this as an observe
    follow-up; v28 stt-nonfinite-passthrough CLOSED it — the inf is now sanitized to
    null in the echoed body (degrade, never 500). Billing (Step 8) fires BEFORE the
    sanitize (Step 8b), so this test's LEDGER invariant is unchanged: the derived 3.0
    is recorded regardless of the body rewrite. The response now SUCCEEDS (200) with a
    null duration, which this test also pins.
    """
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(200, {"duration": math.inf, "text": "hi"})

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    # The record fires inside execute() BEFORE the body is sanitized, so the inf →
    # derived-3.0 LEDGER quantity is captured even though the response now returns 200.
    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=multipart_files(content=make_wav_bytes(seconds=3.0)),
        data={"model": STT_MODEL_ID},
        headers=auth_key(api_key_info["key"]),
    )
    assert resp.status_code == 200, (
        f"inf body must sanitize to null and return 200, not 500, got {resp.status_code}"
    )
    assert resp.json()["duration"] is None, "the non-finite duration is sanitized to null"

    # Flush the asyncio.ensure_future usage-record task.
    for _ in range(50):
        if spy.call_count:
            break
        await asyncio.sleep(0)

    assert spy.call_count == 1, f"expected 1 ledger record, got {spy.call_count}"
    quantity = spy.last_call.get("quantity")
    assert quantity is not None and quantity.is_finite(), (
        f"ledger quantity must be finite (not Infinity) when upstream sends inf, got {quantity}"
    )
    assert quantity == Decimal("3.0"), (
        f"non-finite upstream must fall through to derived Decimal('3.0'), got {quantity}"
    )
