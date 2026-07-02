"""Red suite for the unsupported-input-guard (contract FROZEN @ v1, TASK.md §3).

One test per scenario in TASK.md §2; plus helper unit-tests for the pure guard functions.

Contract assertions:
  - Flag OFF → byte-identical (no lookup, no 400)
  - Flag ON + chat image to text-only model → 400 ERR_UNSUPPORTED_INPUT_MODALITY
    + NO upstream call + NO usage row
  - Flag ON + chat image to vision model → proceeds
  - Flag ON + plain text content → always proceeds
  - Flag ON + video_url part → skipped (deferred from v55) → proceeds
  - Flag ON + alias union (some vision candidate) → allows
  - Flag ON + alias union (all text-only candidates) → 400 + no upstream + no usage
  - Flag ON + STT to text-only model → 400 input_type=audio + no upstream + no usage
  - Flag ON + STT to audio model → proceeds
  - Flag ON + unknown model id → fail-open (no 400)

  Helper unit-tests:
  - required_input_modalities_for_chat: str content, list with text part, list with image part,
    video_url only (→ empty set), mixed text+image
  - resolve_allowed: plain id, alias union, alias all-None
  - enforce: None (pass), no missing (pass), missing → raises
"""

from __future__ import annotations

import io
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COMPLETIONS_PATH = "/v1/chat/completions"
TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _assert_problem(resp: Any, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, (
        f"expected code {code!r}, got {body.get('code')!r}; body={body}"
    )
    assert body.get("status") == status
    return body


# ---------------------------------------------------------------------------
# Helper unit-tests: pure guard functions (RED → functions don't exist yet)
# ---------------------------------------------------------------------------


def test_required_modalities_string_content() -> None:
    """str content → {"text"}."""
    from gateway.proxy.application.modality_guard import required_input_modalities_for_chat

    messages = [{"role": "user", "content": "hello"}]
    assert required_input_modalities_for_chat(messages) == frozenset({"text"})


def test_required_modalities_list_with_text_part() -> None:
    """list with text part → {"text"}."""
    from gateway.proxy.application.modality_guard import required_input_modalities_for_chat

    messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    assert required_input_modalities_for_chat(messages) == frozenset({"text"})


def test_required_modalities_list_with_image_url_part() -> None:
    """list with image_url part → {"text", "image"} (text present too)."""
    from gateway.proxy.application.modality_guard import required_input_modalities_for_chat

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
            ],
        }
    ]
    result = required_input_modalities_for_chat(messages)
    assert "image" in result
    assert "text" in result


def test_required_modalities_image_only_content() -> None:
    """list with only image_url (no text part) → {"image"}."""
    from gateway.proxy.application.modality_guard import required_input_modalities_for_chat

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
            ],
        }
    ]
    result = required_input_modalities_for_chat(messages)
    assert result == frozenset({"image"})


def test_required_modalities_video_url_skipped() -> None:
    """video_url part is skipped → not added to required set."""
    from gateway.proxy.application.modality_guard import required_input_modalities_for_chat

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,..."}},
            ],
        }
    ]
    result = required_input_modalities_for_chat(messages)
    # video_url is ignored → empty frozenset (no text, no image required)
    assert "video" not in result
    assert "image" not in result


@pytest.mark.anyio
async def test_resolve_allowed_plain_id_returns_lookup_result() -> None:
    """resolve_allowed for a plain model id → lookup.get(model_id)."""
    from gateway.proxy.application.modality_guard import resolve_allowed

    class _FakeLookup:
        async def get(self, model_id: str) -> frozenset[str] | None:
            return frozenset({"text"}) if model_id == "m1" else None

    result = await resolve_allowed("m1", _FakeLookup(), model_groups=None)
    assert result == frozenset({"text"})


@pytest.mark.anyio
async def test_resolve_allowed_alias_union() -> None:
    """resolve_allowed for alias → union of candidate allowsets."""
    from gateway.proxy.application.modality_guard import resolve_allowed

    class _FakeLookup:
        async def get(self, model_id: str) -> frozenset[str] | None:
            _data: dict[str, frozenset[str]] = {
                "text-only-a": frozenset({"text"}),
                "vision-b": frozenset({"text", "image"}),
            }
            return _data.get(model_id)

    result = await resolve_allowed(
        "grp", _FakeLookup(), model_groups={"grp": ["text-only-a", "vision-b"]}
    )
    assert result is not None
    assert "image" in result
    assert "text" in result


@pytest.mark.anyio
async def test_resolve_allowed_alias_all_none() -> None:
    """resolve_allowed alias where every candidate has no row → None (fail-open)."""
    from gateway.proxy.application.modality_guard import resolve_allowed

    class _FakeLookup:
        async def get(self, model_id: str) -> frozenset[str] | None:
            return None

    result = await resolve_allowed(
        "grp", _FakeLookup(), model_groups={"grp": ["ghost-a", "ghost-b"]}
    )
    assert result is None


def test_enforce_allowed_none_passes() -> None:
    """enforce(required, None, model_id) → returns silently (fail-open)."""
    from gateway.proxy.application.modality_guard import enforce

    # Should not raise
    enforce(frozenset({"image"}), None, model_id="some-model")


def test_enforce_no_missing_passes() -> None:
    """enforce(required, allowed, model_id) where required ⊆ allowed → no raise."""
    from gateway.proxy.application.modality_guard import enforce

    enforce(frozenset({"text", "image"}), frozenset({"text", "image", "audio"}), model_id="v-model")


def test_enforce_missing_raises_problem_error() -> None:
    """enforce with missing modality → raises ProblemError 400 ERR_UNSUPPORTED_INPUT_MODALITY."""
    from gateway.core.errors import ProblemError
    from gateway.proxy.application.modality_guard import enforce

    with pytest.raises(ProblemError) as exc_info:
        enforce(frozenset({"image"}), frozenset({"text"}), model_id="text-only-x")

    err = exc_info.value
    assert err.status == 400
    assert err.code == "ERR_UNSUPPORTED_INPUT_MODALITY"
    assert "image" in err.detail
    assert "text-only-x" in err.detail


def test_enforce_video_not_enforced() -> None:
    """enforce skips video modality (deferred from v55)."""
    from gateway.proxy.application.modality_guard import enforce

    # video in required but not in allowed → must NOT raise (video is deferred)
    enforce(frozenset({"video"}), frozenset({"text"}), model_id="text-only-x")


# ---------------------------------------------------------------------------
# Integration tests: chat endpoint
# ---------------------------------------------------------------------------


async def test_flag_off_parity(
    app: Any,
    client: Any,
    db_session: Any,
    api_key_info: dict[str, str],
) -> None:
    """Flag OFF → image-to-text-only chat proceeds exactly as today (no 400 from guard)."""
    from tests.input_modality_guard.conftest import (
        CHAT_TEXT_ONLY_MODEL_ID,
        inject_fake_completion_upstream,
        seed_chat_model,
    )

    # Disable guard for this one test
    app.state.settings.input_modality_guard_enabled = False

    await seed_chat_model(db_session, model_id=CHAT_TEXT_ONLY_MODEL_ID, input_modalities="text")
    fake_upstream = inject_fake_completion_upstream(app)

    body = {
        "model": CHAT_TEXT_ONLY_MODEL_ID,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                ],
            }
        ],
    }
    resp = await client.post(COMPLETIONS_PATH, json=body, headers=_auth(api_key_info["key"]))

    # With flag OFF: guard never fires → request passes to upstream
    assert resp.status_code == 200, f"expected 200 (guard off), got {resp.status_code}: {resp.text}"
    assert fake_upstream.calls == 1


async def test_chat_image_to_text_only_rejected(
    app: Any,
    client: Any,
    db_session: Any,
    api_key_info: dict[str, str],
) -> None:
    """Flag ON + image to text-only model → 400 ERR_UNSUPPORTED_INPUT_MODALITY.

    Guard fires BEFORE upstream (no upstream call) and BEFORE billing (no usage row).
    """
    from tests.input_modality_guard.conftest import (
        CHAT_TEXT_ONLY_MODEL_ID,
        SpyRecorder,
        inject_fake_completion_upstream,
        seed_chat_model,
    )

    await seed_chat_model(db_session, model_id=CHAT_TEXT_ONLY_MODEL_ID, input_modalities="text")
    fake_upstream = inject_fake_completion_upstream(app)
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    body = {
        "model": CHAT_TEXT_ONLY_MODEL_ID,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                ],
            }
        ],
    }
    resp = await client.post(COMPLETIONS_PATH, json=body, headers=_auth(api_key_info["key"]))

    body_resp = _assert_problem(resp, 400, "ERR_UNSUPPORTED_INPUT_MODALITY")
    assert "image" in body_resp.get("title", "").lower() or "image" in body_resp.get("detail", "")
    assert fake_upstream.calls == 0, "upstream MUST NOT be called on rejected request"
    assert spy.call_count == 0, "usage row MUST NOT be recorded on rejected request"


async def test_chat_image_to_vision_allowed(
    app: Any,
    client: Any,
    db_session: Any,
    api_key_info: dict[str, str],
) -> None:
    """Flag ON + image to vision model (text,image) → guard allows, proceeds to upstream."""
    from tests.input_modality_guard.conftest import (
        CHAT_VISION_MODEL_ID,
        inject_fake_completion_upstream,
        seed_chat_model,
    )

    await seed_chat_model(db_session, model_id=CHAT_VISION_MODEL_ID, input_modalities="text,image")
    fake_upstream = inject_fake_completion_upstream(app)

    body = {
        "model": CHAT_VISION_MODEL_ID,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                ],
            }
        ],
    }
    resp = await client.post(COMPLETIONS_PATH, json=body, headers=_auth(api_key_info["key"]))

    assert resp.status_code == 200, (
        f"vision model should allow image input, got {resp.status_code}: {resp.text}"
    )
    assert fake_upstream.calls == 1


async def test_chat_text_always_allowed(
    app: Any,
    client: Any,
    db_session: Any,
    api_key_info: dict[str, str],
) -> None:
    """Flag ON + string (text-only) content → never rejected (text is universal)."""
    from tests.input_modality_guard.conftest import (
        CHAT_TEXT_ONLY_MODEL_ID,
        inject_fake_completion_upstream,
        seed_chat_model,
    )

    await seed_chat_model(db_session, model_id=CHAT_TEXT_ONLY_MODEL_ID, input_modalities="text")
    fake_upstream = inject_fake_completion_upstream(app)

    body = {
        "model": CHAT_TEXT_ONLY_MODEL_ID,
        "messages": [{"role": "user", "content": "hello world"}],
    }
    resp = await client.post(COMPLETIONS_PATH, json=body, headers=_auth(api_key_info["key"]))

    assert resp.status_code == 200, (
        f"text content should always pass, got {resp.status_code}: {resp.text}"
    )
    assert fake_upstream.calls == 1


async def test_chat_video_url_skipped(
    app: Any,
    client: Any,
    db_session: Any,
    api_key_info: dict[str, str],
) -> None:
    """Flag ON + text-only model + video_url part → NOT rejected (video deferred from v55)."""
    from tests.input_modality_guard.conftest import (
        CHAT_TEXT_ONLY_MODEL_ID,
        inject_fake_completion_upstream,
        seed_chat_model,
    )

    await seed_chat_model(db_session, model_id=CHAT_TEXT_ONLY_MODEL_ID, input_modalities="text")
    fake_upstream = inject_fake_completion_upstream(app)

    body = {
        "model": CHAT_TEXT_ONLY_MODEL_ID,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,..."}},
                ],
            }
        ],
    }
    resp = await client.post(COMPLETIONS_PATH, json=body, headers=_auth(api_key_info["key"]))

    assert resp.status_code == 200, (
        f"video_url should be skipped (deferred), got {resp.status_code}: {resp.text}"
    )
    assert fake_upstream.calls == 1


@pytest.mark.anyio
async def test_alias_union_allows() -> None:
    """Alias group union at use-case level: some candidate supports image → guard allows.

    Tests _check_input_modalities() directly with a fake lookup and model_groups,
    bypassing HTTP and governance (which requires a DB-wired model router).
    The alias union scenario is also covered by the resolve_allowed unit test above.
    """
    from unittest.mock import AsyncMock, MagicMock

    from gateway.proxy.application.use_cases import CompletionUseCase

    text_model = "text-only-a"
    vision_model = "vision-b"
    alias = "grp"

    class _FakeLookup:
        async def get(self, model_id: str) -> frozenset[str] | None:
            _data: dict[str, frozenset[str]] = {
                text_model: frozenset({"text"}),
                vision_model: frozenset({"text", "image"}),
            }
            return _data.get(model_id)

    uc = CompletionUseCase(
        authenticator=MagicMock(),
        model_checker=MagicMock(),
        input_modality_lookup=_FakeLookup(),
        input_modality_guard_enabled=True,
    )

    body = {
        "model": alias,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                ],
            }
        ],
    }
    model_groups = {alias: [text_model, vision_model]}

    # Should NOT raise — union includes vision-b which supports image
    await uc._check_input_modalities(body, alias, model_groups)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.anyio
async def test_alias_union_rejects() -> None:
    """Alias group union at use-case level: no candidate supports image → raises guard error.

    Tests _check_input_modalities() directly to assert that the guard fires (ProblemError 400)
    when the union of candidates' allowed sets does not include the required modality.
    """
    from unittest.mock import MagicMock

    from gateway.core.errors import ProblemError
    from gateway.proxy.application.use_cases import CompletionUseCase

    text_model_a = "text-only-a2"
    text_model_c = "text-only-c2"
    alias = "grp2"

    class _FakeLookup:
        async def get(self, model_id: str) -> frozenset[str] | None:
            return frozenset({"text"})  # every candidate is text-only

    uc = CompletionUseCase(
        authenticator=MagicMock(),
        model_checker=MagicMock(),
        input_modality_lookup=_FakeLookup(),
        input_modality_guard_enabled=True,
    )

    body = {
        "model": alias,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                ],
            }
        ],
    }
    model_groups = {alias: [text_model_a, text_model_c]}

    # Should raise — no candidate supports image
    with pytest.raises(ProblemError) as exc_info:
        await uc._check_input_modalities(body, alias, model_groups)  # pyright: ignore[reportPrivateUsage]

    err = exc_info.value
    assert err.status == 400
    assert err.code == "ERR_UNSUPPORTED_INPUT_MODALITY"


# ---------------------------------------------------------------------------
# Integration tests: STT endpoint
# ---------------------------------------------------------------------------


async def test_stt_non_audio_rejected(
    app: Any,
    client: Any,
    db_session: Any,
    api_key_info: dict[str, str],
) -> None:
    """Flag ON + STT to text-only model → 400 ERR_UNSUPPORTED_INPUT_MODALITY input_type=audio.

    No upstream call and no usage row.
    """
    from tests.input_modality_guard.conftest import (
        FAKE_AUDIO_BYTES,
        SpyRecorder,
        inject_fake_audio_provider,
        seed_stt_model,
    )

    text_only_stt = "text-only-stt"
    await seed_stt_model(db_session, model_id=text_only_stt, input_modalities="text")
    fake_provider = inject_fake_audio_provider(app, provider_name="openai")
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files={"file": ("audio.mp3", io.BytesIO(FAKE_AUDIO_BYTES), "audio/mpeg")},
        data={"model": text_only_stt},
        headers=_auth(api_key_info["key"]),
    )

    body_resp = _assert_problem(resp, 400, "ERR_UNSUPPORTED_INPUT_MODALITY")
    assert "audio" in body_resp.get("title", "").lower() or "audio" in body_resp.get("detail", "")
    assert fake_provider.post_multipart_calls == 0, "upstream MUST NOT be called on STT reject"
    assert spy.call_count == 0, "usage row MUST NOT be recorded on STT reject"


async def test_stt_audio_allowed(
    app: Any,
    client: Any,
    db_session: Any,
    api_key_info: dict[str, str],
) -> None:
    """Flag ON + STT to audio-capable model → guard allows, proceeds to upstream."""
    from tests.input_modality_guard.conftest import (
        FAKE_AUDIO_BYTES,
        STT_AUDIO_MODEL_ID,
        inject_fake_audio_provider,
        seed_stt_model,
    )

    await seed_stt_model(db_session, model_id=STT_AUDIO_MODEL_ID, input_modalities="audio")
    fake_provider = inject_fake_audio_provider(app, provider_name="openai")

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files={"file": ("audio.mp3", io.BytesIO(FAKE_AUDIO_BYTES), "audio/mpeg")},
        data={"model": STT_AUDIO_MODEL_ID},
        headers=_auth(api_key_info["key"]),
    )

    assert resp.status_code == 200, (
        f"audio-capable model should allow STT, got {resp.status_code}: {resp.text}"
    )
    assert fake_provider.post_multipart_calls == 1


async def test_fail_open_unknown_model(
    app: Any,
    client: Any,
    db_session: Any,
    api_key_info: dict[str, str],
) -> None:
    """Flag ON + unknown model id with image → guard ALLOWS (fail-open on missing capability data).

    The guard never blocks on absent data (design-for-failure).
    The request proceeds to the existing unknown-model error (400 ERR_MODEL_UNKNOWN),
    NOT the guard's 400.
    """
    from tests.input_modality_guard.conftest import inject_fake_completion_upstream

    inject_fake_completion_upstream(app)

    body = {
        "model": "ghost-model-that-does-not-exist",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                ],
            }
        ],
    }
    resp = await client.post(COMPLETIONS_PATH, json=body, headers=_auth(api_key_info["key"]))

    # Guard fails open → existing MODEL_UNKNOWN error fires instead (not the guard's code)
    assert resp.status_code != 400 or resp.json().get("code") != "ERR_UNSUPPORTED_INPUT_MODALITY", (
        "guard must NOT raise ERR_UNSUPPORTED_INPUT_MODALITY for unknown model id"
    )
    # The governance check fires MODEL_UNKNOWN (400) — that's fine; the guard did not fire
    assert resp.json().get("code") != "ERR_UNSUPPORTED_INPUT_MODALITY"
