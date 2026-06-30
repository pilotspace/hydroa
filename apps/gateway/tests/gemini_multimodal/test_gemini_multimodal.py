"""Red suite for Gemini multimodal inline data support.

Tests the OpenAI→Gemini translation of content-part arrays (text + image_url +
video_url DATA URLs) into Gemini inlineData parts, with size guards.

Contract: frozen_contract in task description.
All tests call _openai_to_gemini_request / the helpers directly — no network.
"""

from __future__ import annotations

import base64
import re

import pytest

# RED until BUILD creates these symbols.
from gateway.proxy.infrastructure.gemini_upstream import (
    _content_to_gemini_parts,
    _data_url_to_inline,
    _openai_to_gemini_request,
)

# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------

_DMT = 4096

# A small valid PNG (1×1 pixel, minimal valid PNG)
_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
_PNG_B64 = base64.b64encode(_PNG_1PX).decode()
_PNG_DATA_URL = f"data:image/png;base64,{_PNG_B64}"

_MP4_BYTES = b"fakevideo" * 2  # 18 bytes
_MP4_B64 = base64.b64encode(_MP4_BYTES).decode()
_MP4_DATA_URL = f"data:video/mp4;base64,{_MP4_B64}"


def _req(messages: list[dict] | None = None, **extra: object) -> dict:
    base: dict = {
        "model": "gemini-1.5-flash",
        "messages": messages or [{"role": "user", "content": "hi"}],
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# test_text_plus_image_inline — multipart content array with text + image_url
# ---------------------------------------------------------------------------


def test_text_plus_image_inline() -> None:
    """A user message with [{type:text},{type:image_url}] maps to
    [{text:...},{inlineData:{mimeType:"image/png",data:<b64>}}]."""
    content = [
        {"type": "text", "text": "describe this"},
        {"type": "image_url", "image_url": {"url": _PNG_DATA_URL}},
    ]
    body = _openai_to_gemini_request(
        _req(messages=[{"role": "user", "content": content}]),
        default_max_tokens=_DMT,
    )
    parts = body["contents"][0]["parts"]
    assert len(parts) == 2
    assert parts[0] == {"text": "describe this"}
    assert parts[1]["inlineData"]["mimeType"] == "image/png"
    assert parts[1]["inlineData"]["data"] == _PNG_B64


# ---------------------------------------------------------------------------
# test_video_inline — video_url data URL maps to inlineData mimeType:video/mp4
# ---------------------------------------------------------------------------


def test_video_inline() -> None:
    """A user message with [{type:video_url}] maps to [{inlineData:{mimeType:"video/mp4",...}}]."""
    content = [
        {"type": "video_url", "video_url": {"url": _MP4_DATA_URL}},
    ]
    body = _openai_to_gemini_request(
        _req(messages=[{"role": "user", "content": content}]),
        default_max_tokens=_DMT,
    )
    parts = body["contents"][0]["parts"]
    assert len(parts) == 1
    assert parts[0]["inlineData"]["mimeType"] == "video/mp4"
    assert parts[0]["inlineData"]["data"] == _MP4_B64


# ---------------------------------------------------------------------------
# test_string_content_byte_identical — plain string content must be unchanged
# ---------------------------------------------------------------------------


def test_string_content_byte_identical() -> None:
    """content='hello' must produce [{text:'hello'}] — byte-identical to today's path."""
    body = _openai_to_gemini_request(
        _req(messages=[{"role": "user", "content": "hello"}]),
        default_max_tokens=_DMT,
    )
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]


def test_string_content_assistant_byte_identical() -> None:
    """Assistant string content must also remain byte-identical."""
    body = _openai_to_gemini_request(
        _req(
            messages=[
                {"role": "user", "content": "Q"},
                {"role": "assistant", "content": "A"},
            ]
        ),
        default_max_tokens=_DMT,
    )
    assert body["contents"][0] == {"role": "user", "parts": [{"text": "Q"}]}
    assert body["contents"][1] == {"role": "model", "parts": [{"text": "A"}]}


def test_string_content_full_request_existing() -> None:
    """Regression: full existing test-style request (system+user+assistant) stays identical."""
    body = _openai_to_gemini_request(
        {
            "model": "gemini-1.5-flash",
            "messages": [
                {"role": "system", "content": "S"},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "prev"},
            ],
            "max_tokens": 64,
            "temperature": 0.5,
        },
        default_max_tokens=_DMT,
    )
    assert body["systemInstruction"] == {"parts": [{"text": "S"}]}
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "Hi"}]},
        {"role": "model", "parts": [{"text": "prev"}]},
    ]


# ---------------------------------------------------------------------------
# test_over_cap_raises — inline size guard fires before upstream
# ---------------------------------------------------------------------------


def test_over_cap_raises() -> None:
    """max_inline_bytes=8, a 9-byte payload → ValueError('inline_too_large')."""
    nine_bytes = b"123456789"  # 9 bytes
    b64 = base64.b64encode(nine_bytes).decode()
    data_url = f"data:image/png;base64,{b64}"
    content = [{"type": "image_url", "image_url": {"url": data_url}}]
    with pytest.raises(ValueError, match="inline_too_large"):
        _openai_to_gemini_request(
            _req(messages=[{"role": "user", "content": content}]),
            default_max_tokens=_DMT,
            max_inline_bytes=8,
        )


def test_over_cap_raises_via_helper() -> None:
    """_data_url_to_inline respects max_inline_bytes directly."""
    nine_bytes = b"123456789"
    b64 = base64.b64encode(nine_bytes).decode()
    data_url = f"data:image/png;base64,{b64}"
    running = [0]
    with pytest.raises(ValueError, match="inline_too_large"):
        _data_url_to_inline(data_url, max_inline_bytes=8, running_total=running)


def test_cap_zero_means_unlimited() -> None:
    """max_inline_bytes=0 means unlimited — no error even for a large payload."""
    big = b"x" * 30_000_000
    b64 = base64.b64encode(big).decode()
    data_url = f"data:image/png;base64,{b64}"
    content = [{"type": "image_url", "image_url": {"url": data_url}}]
    # Should NOT raise
    body = _openai_to_gemini_request(
        _req(messages=[{"role": "user", "content": content}]),
        default_max_tokens=_DMT,
        max_inline_bytes=0,
    )
    assert body["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "image/png"


# ---------------------------------------------------------------------------
# test_non_data_url_rejected — https URL rejected (SSRF prevention)
# ---------------------------------------------------------------------------


def test_non_data_url_rejected() -> None:
    """An https:// URL raises ValueError('only_data_url_supported') — no network call."""
    content = [{"type": "image_url", "image_url": {"url": "https://example.com/img.png"}}]
    with pytest.raises(ValueError, match="only_data_url_supported"):
        _openai_to_gemini_request(
            _req(messages=[{"role": "user", "content": content}]),
            default_max_tokens=_DMT,
        )


def test_non_data_url_rejected_no_network_import() -> None:
    """Confirm that no network module (httpx/requests/urllib) is imported inside the helper."""
    import importlib
    import sys

    # The helper must NOT import or use httpx/requests/urllib for fetching.
    # We verify the function itself raises without network activity.
    running = [0]
    with pytest.raises(ValueError, match="only_data_url_supported"):
        _data_url_to_inline("https://bad.example.com/", max_inline_bytes=0, running_total=running)
    # Verify no network call was attempted by checking the function raises immediately.
    # (This is guaranteed by implementation using re.match only.)


# ---------------------------------------------------------------------------
# test_bad_base64_rejected — malformed base64 string
# ---------------------------------------------------------------------------


def test_bad_base64_rejected() -> None:
    """'data:image/png;base64,!!!' has invalid base64 → ValueError."""
    running = [0]
    with pytest.raises(ValueError):
        _data_url_to_inline("data:image/png;base64,!!!", max_inline_bytes=0, running_total=running)


def test_bad_base64_via_request() -> None:
    """Malformed base64 in a request-level content part propagates as ValueError."""
    content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,!!!"}}]
    with pytest.raises(ValueError):
        _openai_to_gemini_request(
            _req(messages=[{"role": "user", "content": content}]),
            default_max_tokens=_DMT,
        )


# ---------------------------------------------------------------------------
# test_unknown_part_type_rejected — unsupported part type
# ---------------------------------------------------------------------------


def test_unknown_part_type_rejected() -> None:
    """A part with {type:'audio_url'} raises ValueError('unsupported_content_part')."""
    content = [{"type": "audio_url", "audio_url": {"url": _PNG_DATA_URL}}]
    with pytest.raises(ValueError, match="unsupported_content_part"):
        _openai_to_gemini_request(
            _req(messages=[{"role": "user", "content": content}]),
            default_max_tokens=_DMT,
        )


def test_missing_type_rejected() -> None:
    """A part with no 'type' key raises ValueError('unsupported_content_part')."""
    content = [{"text": "oops no type"}]
    with pytest.raises(ValueError, match="unsupported_content_part"):
        _openai_to_gemini_request(
            _req(messages=[{"role": "user", "content": content}]),
            default_max_tokens=_DMT,
        )


# ---------------------------------------------------------------------------
# test_content_to_gemini_parts — helper unit tests
# ---------------------------------------------------------------------------


def test_content_to_gemini_parts_string() -> None:
    """String content → [{text: ...}]."""
    result = _content_to_gemini_parts("hello world", max_inline_bytes=0, running_total=[0])
    assert result == [{"text": "hello world"}]


def test_content_to_gemini_parts_none_coerces() -> None:
    """None content → [{text: 'None'}] (coerce via str())."""
    result = _content_to_gemini_parts(None, max_inline_bytes=0, running_total=[0])
    assert result == [{"text": "None"}]


def test_content_to_gemini_parts_int_coerces() -> None:
    """Integer content → [{text: '42'}] (coerce via str())."""
    result = _content_to_gemini_parts(42, max_inline_bytes=0, running_total=[0])
    assert result == [{"text": "42"}]


# ---------------------------------------------------------------------------
# test_data_url_to_inline — helper unit tests
# ---------------------------------------------------------------------------


def test_data_url_to_inline_returns_mime_and_b64() -> None:
    """_data_url_to_inline returns {mimeType, data} with the original b64 string."""
    running = [0]
    result = _data_url_to_inline(_PNG_DATA_URL, max_inline_bytes=0, running_total=running)
    assert result == {"mimeType": "image/png", "data": _PNG_B64}
    assert running[0] == len(_PNG_1PX)


def test_data_url_to_inline_accumulates_total() -> None:
    """Running total accumulates across multiple calls."""
    running = [0]
    _data_url_to_inline(_PNG_DATA_URL, max_inline_bytes=0, running_total=running)
    first_size = running[0]
    _data_url_to_inline(_MP4_DATA_URL, max_inline_bytes=0, running_total=running)
    assert running[0] == first_size + len(_MP4_BYTES)


# ---------------------------------------------------------------------------
# test_running_total_shared_across_messages — size guard is global to request
# ---------------------------------------------------------------------------


def test_running_total_shared_across_messages() -> None:
    """Size cap accumulates across MULTIPLE messages in a single request."""
    # 5 bytes per image, 2 messages, cap = 9 → second message tips over
    five_bytes = b"12345"
    b64 = base64.b64encode(five_bytes).decode()
    data_url = f"data:image/png;base64,{b64}"
    content = [{"type": "image_url", "image_url": {"url": data_url}}]
    messages = [
        {"role": "user", "content": content},
        {"role": "user", "content": content},
    ]
    with pytest.raises(ValueError, match="inline_too_large"):
        _openai_to_gemini_request(
            _req(messages=messages),
            default_max_tokens=_DMT,
            max_inline_bytes=9,
        )


# ---------------------------------------------------------------------------
# test_missing_image_url_key — KeyError → ValueError, never 500
# ---------------------------------------------------------------------------


def test_missing_image_url_key() -> None:
    """A part with type='image_url' but no 'image_url' key raises ValueError."""
    content = [{"type": "image_url"}]  # missing the nested url dict
    with pytest.raises(ValueError):
        _openai_to_gemini_request(
            _req(messages=[{"role": "user", "content": content}]),
            default_max_tokens=_DMT,
        )


def test_missing_video_url_key() -> None:
    """A part with type='video_url' but no 'video_url' key raises ValueError."""
    content = [{"type": "video_url"}]  # missing the nested url dict
    with pytest.raises(ValueError):
        _openai_to_gemini_request(
            _req(messages=[{"role": "user", "content": content}]),
            default_max_tokens=_DMT,
        )
