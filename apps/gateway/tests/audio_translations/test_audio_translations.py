"""RED→GREEN suite for POST /v1/audio/translations (Whisper translate-to-English).

No-DB unit tests of TranscriptionUseCase.execute with mocked collaborators.
All four scenarios test the new `upstream_path` parameter.

RED at write time: TranscriptionUseCase.execute has no `upstream_path` kwarg →
TypeError on every call; `language` is always forwarded regardless of path.
"""

from __future__ import annotations

import types
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from gateway.proxy.application.audio_use_case import TranscriptionUseCase


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------


class _StubGovernance:
    """Minimal NonChatGovernance stub — returns a fixed authz result."""

    async def authorize(
        self,
        raw_key: str | None,
        model_id: str,
        estimated_tokens: int | None = None,
    ) -> Any:
        return types.SimpleNamespace(
            tenant_id=uuid.uuid4(),
            key_id=uuid.uuid4(),
            team_id=None,
        )


class _FakeSession:
    """Async DB session stub — always returns a row with modality=audio_stt / provider=openai."""

    async def execute(self, stmt: Any) -> Any:
        row = types.SimpleNamespace(modality="audio_stt", provider="openai")

        class _Result:
            def one_or_none(self) -> Any:
                return row

        return _Result()


class _SpyProviderAdapter:
    """Provider adapter spy — records path + data, returns a fixed 200 response."""

    def __init__(self) -> None:
        self.captured_path: str = ""
        self.captured_data: dict[str, Any] = {}

    async def post_multipart(
        self,
        path: str,
        *,
        files: dict[str, Any],
        data: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        self.captured_path = path
        self.captured_data = dict(data)
        return 200, {"text": "hello", "duration": 1.0}


class _FakeFile:
    """Minimal UploadFile-like fake."""

    filename = "audio.mp3"
    content_type = "audio/mpeg"

    async def read(self) -> bytes:
        return b"\x00"


def _make_form(**extra: str) -> dict[str, Any]:
    """Return a plain dict acting as the multipart form (form.get used everywhere)."""
    base: dict[str, Any] = {"file": _FakeFile(), "model": "whisper-1"}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def spy() -> _SpyProviderAdapter:
    return _SpyProviderAdapter()


@pytest.fixture()
def fire_record_calls() -> list[dict[str, Any]]:
    return []


@pytest.fixture()
def use_case(
    spy: _SpyProviderAdapter,
    fire_record_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> TranscriptionUseCase:
    """Construct a TranscriptionUseCase with all collaborators monkeypatched."""
    # Patch select_provider in the use-case module namespace → return the spy.
    monkeypatch.setattr(
        "gateway.proxy.application.audio_use_case.select_provider",
        lambda modality, provider, registry: spy,
    )

    # Patch _fire_record_with_raw → append kwargs to fire_record_calls.
    def _capture_fire(recorder: Any, **kwargs: Any) -> None:
        fire_record_calls.append(kwargs)

    monkeypatch.setattr(
        "gateway.proxy.application.audio_use_case._fire_record_with_raw",
        _capture_fire,
    )

    return TranscriptionUseCase(
        governance=_StubGovernance(),  # type: ignore[arg-type]
        session=_FakeSession(),  # type: ignore[arg-type]
        tenant_credential_resolver=None,
    )


# Stub usage recorder (never actually called — _fire_record_with_raw is patched)
_STUB_RECORDER = MagicMock()
_STUB_REGISTRY = MagicMock()


# ---------------------------------------------------------------------------
# Test 1 — translations path is forwarded to the provider adapter
# ---------------------------------------------------------------------------


async def test_translate_routes_to_translations_path(
    use_case: TranscriptionUseCase,
    spy: _SpyProviderAdapter,
) -> None:
    """execute(upstream_path='/audio/translations') must call post_multipart with that path."""
    form = _make_form()
    await use_case.execute(
        raw_key="sk-test",
        form=form,
        registry=_STUB_REGISTRY,
        usage_recorder=_STUB_RECORDER,
        upstream_path="/audio/translations",
    )
    assert spy.captured_path == "/audio/translations", (
        f"expected /audio/translations, got {spy.captured_path!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — translations drops `language`, keeps other passthrough fields
# ---------------------------------------------------------------------------


async def test_translate_drops_language_field(
    use_case: TranscriptionUseCase,
    spy: _SpyProviderAdapter,
) -> None:
    """When upstream_path='/audio/translations', `language` must NOT be forwarded."""
    form = _make_form(language="es", temperature="0")
    await use_case.execute(
        raw_key="sk-test",
        form=form,
        registry=_STUB_REGISTRY,
        usage_recorder=_STUB_RECORDER,
        upstream_path="/audio/translations",
    )
    assert "language" not in spy.captured_data, (
        "language must be dropped for /audio/translations"
    )
    assert "temperature" in spy.captured_data, (
        "temperature must still be forwarded for /audio/translations"
    )


# ---------------------------------------------------------------------------
# Test 3 — default (transcriptions) path is byte-identical, language forwarded
# ---------------------------------------------------------------------------


async def test_transcription_default_path_unchanged(
    use_case: TranscriptionUseCase,
    spy: _SpyProviderAdapter,
) -> None:
    """Calling execute() with NO upstream_path must behave byte-identically to before."""
    form = _make_form(language="fr")
    await use_case.execute(
        raw_key="sk-test",
        form=form,
        registry=_STUB_REGISTRY,
        usage_recorder=_STUB_RECORDER,
        # No upstream_path kwarg → must default to /audio/transcriptions
    )
    assert spy.captured_path == "/audio/transcriptions", (
        f"default path must be /audio/transcriptions, got {spy.captured_path!r}"
    )
    assert "language" in spy.captured_data, (
        "language must be forwarded on the default transcriptions path"
    )


# ---------------------------------------------------------------------------
# Test 4 — billing always uses pricing_unit="per_second" for translations
# ---------------------------------------------------------------------------


async def test_translate_bills_per_second(
    use_case: TranscriptionUseCase,
    fire_record_calls: list[dict[str, Any]],
) -> None:
    """The _fire_record_with_raw call for /audio/translations must use pricing_unit='per_second'."""
    form = _make_form()
    await use_case.execute(
        raw_key="sk-test",
        form=form,
        registry=_STUB_REGISTRY,
        usage_recorder=_STUB_RECORDER,
        upstream_path="/audio/translations",
    )
    assert len(fire_record_calls) == 1, (
        f"expected exactly one billing record, got {len(fire_record_calls)}"
    )
    assert fire_record_calls[0].get("pricing_unit") == "per_second", (
        f"expected pricing_unit='per_second', got {fire_record_calls[0].get('pricing_unit')!r}"
    )
