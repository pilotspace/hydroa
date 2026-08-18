"""Red->green suite for upload-bounds-audio (TASK.md §CHECKS — SECURITY).

One test per §CHECKS row. Asserts observable behavior only: HTTP status, problem `code`,
provider/recorder call counts, middleware configuration, and a source-level sweep —
never implementation internals beyond the contracted seams.

RED at the current tree: the /v1/audio/ route cap equals max_audio_upload_bytes (no
multipart headroom), so the edge pre-empts every boundary case with
ERR_REQUEST_BODY_TOO_LARGE; ERR_PAYLOAD_AUDIO_TOO_LARGE, `_audio_route_cap`, and the
capped audio reader do not exist. DO NOT weaken these tests to pass — that is Build's job.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    SMALL_AUDIO_CAP,
    STT_MODEL_ID,
    STT_RESPONSE_BODY,
    TRANSCRIPTIONS,
    TRANSLATIONS,
    SpyRecorder,
    assert_problem,
    auth_key,
    inject_fake_openai_audio_provider,
    multipart_audio,
    seed_stt_model,
)

pytestmark = pytest.mark.asyncio

# The headroom the contract requires the /v1/audio/ route cap to carry over the per-file
# cap (M3) — mirrors main._FILES_MULTIPART_HEADROOM_BYTES. Our multipart framing (one
# file part + one model field) is a few hundred bytes, far below this.
HEADROOM = 1024 * 1024


async def _prepped(bounds_app, db_session, api_key):
    """Common arrangement: seeded STT model, faked provider, spy recorder."""
    application, client = bounds_app
    await seed_stt_model(db_session)
    provider = inject_fake_openai_audio_provider(application)
    provider.set_multipart_response(200, STT_RESPONSE_BODY)
    spy = SpyRecorder()
    application.state.usage_recorder = spy
    return application, client, provider, spy


# ── S1 · per-file cap ──────────────────────────────────────────────────────────


async def test_oversized_transcription_413_audio_code(bounds_app, db_session, api_key) -> None:
    """covers: M1, M3, A1, A3, A6, E1, R:BOUNDARY_THEATER — a file ONE byte over
    max_audio_upload_bytes (total body well within cap+headroom) is refused by the
    PER-FILE check with 413 ERR_PAYLOAD_AUDIO_TOO_LARGE. RED today: the equal route cap
    pre-empts at the edge with ERR_REQUEST_BODY_TOO_LARGE — the deadcode shape
    R:BOUNDARY_THEATER names."""
    _, client, _, _ = await _prepped(bounds_app, db_session, api_key)
    resp = await client.post(
        TRANSCRIPTIONS,
        files=multipart_audio(b"\0" * (SMALL_AUDIO_CAP + 1)),
        data={"model": STT_MODEL_ID},
        headers=auth_key(api_key),
    )
    assert_problem(resp, 413, "ERR_PAYLOAD_AUDIO_TOO_LARGE")


async def test_oversized_translation_413_audio_code(bounds_app, db_session, api_key) -> None:
    """covers: M1, A2, E2 — the translations route (same use case, upstream_path switch)
    refuses an oversized file with the same per-file 413."""
    _, client, _, _ = await _prepped(bounds_app, db_session, api_key)
    resp = await client.post(
        TRANSLATIONS,
        files=multipart_audio(b"\0" * (SMALL_AUDIO_CAP + 1)),
        data={"model": STT_MODEL_ID},
        headers=auth_key(api_key),
    )
    assert_problem(resp, 413, "ERR_PAYLOAD_AUDIO_TOO_LARGE")


async def test_exact_cap_file_reaches_handler(bounds_app, db_session, api_key) -> None:
    """covers: M3, A3, E3 — a raw file of EXACTLY max_audio_upload_bytes must NOT be
    pre-empted by the edge (multipart framing rides in the headroom): the faked upstream
    answers 200. RED today: framing pushes the body over the equal route cap -> 413."""
    _, client, provider, _ = await _prepped(bounds_app, db_session, api_key)
    resp = await client.post(
        TRANSCRIPTIONS,
        files=multipart_audio(b"\0" * SMALL_AUDIO_CAP),
        data={"model": STT_MODEL_ID},
        headers=auth_key(api_key),
    )
    assert resp.status_code == 200, f"exact-cap file must pass: {resp.status_code} {resp.text}"
    assert len(provider.post_multipart_calls) == 1


async def test_oversized_rejected_before_upstream_and_billing(
    bounds_app, db_session, api_key
) -> None:
    """covers: M2, A5, E5, R:BILLED_OVERSIZE — the per-file reject leaves the provider and
    the usage recorder completely untouched."""
    _, client, provider, spy = await _prepped(bounds_app, db_session, api_key)
    resp = await client.post(
        TRANSCRIPTIONS,
        files=multipart_audio(b"\0" * (SMALL_AUDIO_CAP + 1)),
        data={"model": STT_MODEL_ID},
        headers=auth_key(api_key),
    )
    assert resp.status_code == 413, f"expected 413, got {resp.status_code}: {resp.text}"
    assert resp.json().get("code") == "ERR_PAYLOAD_AUDIO_TOO_LARGE"
    assert provider.post_multipart_calls == [], "oversized upload must never reach upstream"
    assert spy.call_count == 0, "oversized upload must never produce a usage record"


async def test_edge_cap_still_authoritative_beyond_headroom(
    bounds_app, db_session, api_key
) -> None:
    """covers: M4, E4, R:WEAKENED_EDGE — two-phase so this is RED today for the right
    reason. Phase 1 (red driver): a body BETWEEN cap and cap+headroom is NOT
    edge-rejected — it reaches the handler and gets the per-file code (today the edge
    kills it). Phase 2 (pin): a body beyond cap+headroom is still refused at the edge
    with ERR_REQUEST_BODY_TOO_LARGE and zero provider/recorder work — the coarse layer
    must never be weakened to make phase 1 pass."""
    _, client, provider, spy = await _prepped(bounds_app, db_session, api_key)

    in_headroom = await client.post(
        TRANSCRIPTIONS,
        files=multipart_audio(b"\0" * (SMALL_AUDIO_CAP + 1)),
        data={"model": STT_MODEL_ID},
        headers=auth_key(api_key),
    )
    assert_problem(in_headroom, 413, "ERR_PAYLOAD_AUDIO_TOO_LARGE")

    beyond = await client.post(
        TRANSCRIPTIONS,
        files=multipart_audio(b"\0" * (SMALL_AUDIO_CAP + HEADROOM + 4096)),
        data={"model": STT_MODEL_ID},
        headers=auth_key(api_key),
    )
    assert_problem(beyond, 413, "ERR_REQUEST_BODY_TOO_LARGE")
    assert provider.post_multipart_calls == []
    assert spy.call_count == 0


async def test_cap_zero_disables_per_file_check_only(bounds_app) -> None:
    """covers: M6, A4, E6 — 0 disables the PER-FILE check (images/tts escape-hatch
    convention) while the route-cap helper returns a large FINITE ceiling, never
    0/unlimited. RED today: neither symbol exists (ImportError)."""
    from gateway.main import _audio_route_cap
    from gateway.proxy.application.audio_use_case import _read_capped_audio_upload

    ceiling = _audio_route_cap(0)
    assert ceiling > 0, "cap=0 must never yield a 0 (block-everything) route cap"
    assert ceiling < 2**40, "cap=0 must map to a FINITE ceiling, never effectively unlimited"
    assert _audio_route_cap(SMALL_AUDIO_CAP) == SMALL_AUDIO_CAP + HEADROOM

    class _Field:
        def __init__(self, data: bytes) -> None:
            self._data = data

        async def read(self, size: int = -1) -> bytes:
            return self._data if size is None or size < 0 else self._data[:size]

    blob = b"\0" * (SMALL_AUDIO_CAP * 2)
    assert await _read_capped_audio_upload(_Field(blob), 0) == blob  # 0 ⇒ unchecked


# ── S2 · structural multipart-bounds guard ─────────────────────────────────────


def _gateway_src() -> Path:
    import gateway

    return Path(gateway.__file__).resolve().parent


async def test_multipart_surface_structurally_bounded(bounds_app) -> None:
    """covers: M5, A7, A8, A10, A12, E7, R:UNBOUNDED_READ — two structural pins:

    1. Middleware config: BodySizeLimitMiddleware is registered with a FINITE cap for
       every multipart-consuming prefix and a finite default_cap.
    2. Source sweep: every `await <field>.read(` on a request-body upload field under
       proxy/application + files/api sits inside an ALLOWLISTED bounded reader. A new
       read site fails here until it is bounded and allowlisted (SANCTIONED EDIT
       comment convention — cite the task that bounds it).

    RED today: audio_use_case's file read lives in `execute` (unbounded, not
    allowlisted)."""
    application, _ = bounds_app

    # ── 1. middleware config pin ──
    caps_entry = None
    for m in application.user_middleware:
        if m.cls.__name__ == "BodySizeLimitMiddleware":
            caps_entry = m.kwargs
            break
    assert caps_entry is not None, "BodySizeLimitMiddleware must be registered"
    route_caps: dict[str, int] = dict(caps_entry["route_caps"])
    default_cap: int = caps_entry["default_cap"]
    for prefix in ("/v1/audio/", "/v1/files", "/v1/", "/admin/"):
        cap = route_caps.get(prefix)
        assert isinstance(cap, int) and 0 < cap < 2**40, (
            f"route cap for {prefix!r} must be present and finite; got {cap!r}"
        )
    assert isinstance(default_cap, int) and 0 < default_cap < 2**40

    # ── 2. bounded-reader source sweep ──
    # (relative posix path, enclosing function) pairs sanctioned as BOUNDED readers.
    # SANCTIONED EDIT rules: add a pair ONLY together with the bound that covers it,
    # citing the task — an unlisted `.read(` on an upload field is R:UNBOUNDED_READ.
    allowlist = {
        # images: read-then-check helper, PAYLOAD_IMAGE_TOO_LARGE (image-edits task)
        ("proxy/application/images_use_case.py", "_read_capped_upload"),
        # files: bounded read(max_bytes+1) + FILE_TOO_LARGE (files-uploads-api task)
        ("files/api/router.py", "create_file"),
        # audio: capped reader, PAYLOAD_AUDIO_TOO_LARGE (upload-bounds-audio task)
        ("proxy/application/audio_use_case.py", "_read_capped_audio_upload"),
    }
    src = _gateway_src()
    read_pattern = re.compile(r"await\s+\w[\w.]*\.read\(")
    def_pattern = re.compile(r"^(\s*)(?:async\s+)?def\s+(\w+)")
    violations: list[str] = []
    for base in ("proxy/application", "files/api"):
        for path in sorted((src / base).glob("*.py")):
            lines = path.read_text().splitlines()
            for lineno, line in enumerate(lines):
                if not read_pattern.search(line):
                    continue
                enclosing = None
                indent = len(line) - len(line.lstrip())
                for back in range(lineno, -1, -1):
                    m = def_pattern.match(lines[back])
                    if m and len(m.group(1)) < indent:
                        enclosing = m.group(2)
                        break
                rel = path.relative_to(src).as_posix()
                if (rel, enclosing) not in allowlist:
                    violations.append(f"{rel}:{lineno + 1} in {enclosing}() — {line.strip()}")
    assert violations == [], (
        "unbounded (un-allowlisted) request-body read sites found:\n" + "\n".join(violations)
    )
