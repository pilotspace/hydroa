"""Red/green suite for the streaming output PII mask defect (HIGH remediation A3).

Defect: `CompletionUseCase.stream()` never applied post-call guardrail masking
(`evaluate_post`) to the streamed SSE bytes — `complete()` and the cache-hit paths
mask the model's output, but `stream()` logged `streaming_pii_mask_skipped` and sent
the raw, UNMASKED text straight to the client. A `stream:true` request bypassed a
tenant's `pii_mask=mask` guardrail on the majority traffic pattern.

Fix under test: `CompletionUseCase.stream()` now buffers the completed SSE stream,
runs the assembled assistant text through `evaluate_post` (same fail-OPEN semantics
as `complete()`), and rewrites the SSE frames so the client only ever sees masked
text when a `pii_mask=mask` guardrail is configured. When NO post-call guardrail is
configured, the path is byte-identical to before (no buffering, no extra latency).

Test-DB: GATEWAY_TEST_DATABASE_URL=postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test_rem_a3
Follows the exact fixture/helper conventions of tests/guardrails/test_guardrails_core.py
(S15 is the non-streaming sibling of test_streaming_post_call_pii_mask_masks_email below).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.keys.domain.entities import AuthzResult
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.infrastructure.guardrail_evaluator import RegexGuardrailEvaluator
from tests.streaming_resilience.conftest import (
    KEY_ID,
    PLAIN,
    TENANT_A,
    FakeModelChecker,
    FakeUsageRecorder,
    PlanStreamUpstream,
    make_payload,
)

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
ADMIN_GUARDRAILS = "/admin/guardrails"
COMPLETIONS = "/v1/chat/completions"

# SSE delta stream whose concatenated assistant text contains a PII email address,
# split across multiple delta chunks (as a real provider stream would) so the fix
# must assemble the FULL text before masking — a naive per-chunk regex would miss an
# email address split across chunk boundaries.
PII_SSE_CHUNKS = [
    b'data: {"id":"gen-stream-pii","choices":[{"delta":{"content":"Your email is "}}]}\n\n',
    b'data: {"id":"gen-stream-pii","choices":[{"delta":{"content":"user@example."}}]}\n\n',
    b'data: {"id":"gen-stream-pii","choices":[{"delta":{"content":"com - confirmed."}}]}\n\n',
    b'data: {"usage":{"prompt_tokens":12,"completion_tokens":8,"total_tokens":20}}\n\n',
    b"data: [DONE]\n\n",
]

# Content-free-of-PII SSE stream used for the no-guardrail-configured byte-identical
# regression check.
CLEAN_SSE_CHUNKS = [
    b'data: {"id":"gen-stream-clean","choices":[{"delta":{"content":"h"}}]}\n\n',
    b'data: {"id":"gen-stream-clean","choices":[{"delta":{"content":"i"}}]}\n\n',
    b'data: {"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n',
    b"data: [DONE]\n\n",
]


class FakeStreamUpstream:
    """Fake upstream whose stream() replays a fixed list of raw SSE byte chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:  # pragma: no cover
        raise AssertionError("non-streaming complete() must not be called by these tests")

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            for chunk in self.chunks:
                yield chunk

        return _gen()


def auth_jwt(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def signup_and_login(
    client: httpx.AsyncClient, *, tenant_name: str, email: str, password: str = "correct horse battery"
) -> tuple[str, str]:
    sr = await client.post(SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": password})
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


async def create_key(client: httpx.AsyncClient, jwt: str, *, name: str) -> dict[str, Any]:
    resp = await client.post(ADMIN_KEYS, json={"name": name}, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_key failed ({resp.status_code}): {resp.text}"
    return resp.json()


async def set_guardrail_config(client: httpx.AsyncClient, jwt: str, config: dict[str, Any]) -> None:
    resp = await client.put(ADMIN_GUARDRAILS, json=config, headers=auth_jwt(jwt))
    assert resp.status_code == 200, f"PUT /admin/guardrails failed ({resp.status_code}): {resp.text}"


def completion_payload(model: str, content: str, *, stream: bool = True) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "stream": stream,
    }


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    model_id = "openai/gpt-4o-mini"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true) ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o-mini"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:sid, :mid, :p, :c, now()) ON CONFLICT DO NOTHING"
        ),
        {"sid": str(uuid.uuid4()), "mid": model_id, "p": "0.000001", "c": "0.000002"},
    )
    await db_session.commit()
    return model_id


async def test_streaming_post_call_pii_mask_masks_email(
    client: httpx.AsyncClient, app: Any, active_model: str
) -> None:
    """stream:true + pii_mask=mask guardrail → client never sees the raw email.

    RED (before fix): CompletionUseCase.stream() never calls evaluate_post — the raw
    "user@example.com" reaches the client unmasked, byte-identical to the upstream's
    SSE bytes. This assertion fails against current code.
    GREEN (after fix): the streamed content is masked to [EMAIL_REDACTED] before it
    leaves the gateway, matching what complete() already does for non-streaming.
    """
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="StreamPostMaskCo", email="owner@streampostmask.io"
    )
    key_info = await create_key(client, jwt, name="stream-postmask-key")
    await set_guardrail_config(client, jwt, {"pii_mask": {"enabled": True, "mode": "mask"}})

    upstream = FakeStreamUpstream(PII_SSE_CHUNKS)
    app.state.completion_upstream = upstream

    payload = completion_payload(active_model, "what is my registered email?")
    resp = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_info["key"]))

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert resp.headers["content-type"].startswith("text/event-stream")
    raw = resp.content

    assert b"user@example.com" not in raw, (
        f"streaming output PII mask defect: raw email leaked into streamed SSE bytes: {raw!r}"
    )
    assert b"[EMAIL_REDACTED]" in raw, (
        f"expected masked token in streamed SSE bytes, got: {raw!r}"
    )
    # Protocol correctness: still a well-formed, terminated SSE stream.
    assert raw.endswith(b"data: [DONE]\n\n")
    # Usage frame must still be present/parseable — masking must not corrupt billing data.
    assert b'"total_tokens":20' in raw
    assert upstream.calls == 1


async def test_streaming_no_guardrail_configured_byte_identical(
    client: httpx.AsyncClient, app: Any, active_model: str
) -> None:
    """No post-call guardrail configured → streaming path is byte-identical (no buffering).

    Guards the "common no-guardrail case" requirement: when no guardrail is configured
    at all, stream() must yield the exact upstream bytes it always has — the fix must not
    add buffering overhead or alter output when there is nothing to mask.
    """
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="StreamNoGuardCo", email="owner@streamnoguard.io"
    )
    key_info = await create_key(client, jwt, name="stream-noguard-key")
    # Deliberately do NOT call set_guardrail_config — no guardrails configured at all.

    upstream = FakeStreamUpstream(CLEAN_SSE_CHUNKS)
    app.state.completion_upstream = upstream

    payload = completion_payload(active_model, "hello")
    resp = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_info["key"]))

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert resp.content == b"".join(CLEAN_SSE_CHUNKS), (
        "no guardrail configured: streamed bytes must be byte-identical to upstream"
    )


# ===========================================================================
# Adversarial-verification follow-up (2 holes found in the first pass):
#
# HOLE 1 (n>1 corruption): _rewrite_sse_content's `assigned` flag was call-wide,
# not per-choice, and the text fed to evaluate_post was extract_content_from_sse's
# ALL-CHOICES-MERGED string. For n=2 streaming with pii_mask=mask: choice 0 got a
# masked blob merging BOTH candidates' text and choice 1 was blanked to "" — this
# diverges from complete()'s _mask_pii_in_body, which masks each choice's content
# INDEPENDENTLY.
#
# HOLE 2 (silent total loss): when _post_mask_active buffers, content was only
# flushed on BandwidthExhaustedError, (UpstreamUnavailableError, CircuitOpenError),
# or clean completion. Any OTHER exception type propagated with the ENTIRE buffered
# (already-generated) response silently dropped — pre-fix (non-masking mode) the
# client got the live prefix; post-fix (masking mode, before this hole was closed)
# the client got NOTHING.
#
# These two tests drive CompletionUseCase.stream() directly (unit-level, no HTTP/DB),
# mirroring tests/stream_upstream_error_frame/test_stream_upstream_error_frame.py's
# established harness — the exact internal generator-branch behavior under test here
# isn't reliably observable through a full ASGI/httpx round trip once the app raises
# mid-stream.
# ===========================================================================


class _GuardrailAuthenticator:
    """KeyAuthenticator stub returning an AuthzResult carrying guardrail_configs.

    Mirrors tests/streaming_resilience/conftest.py's FakeAuthenticator exactly, plus
    the one extra field these tests need: a populated guardrail_configs so
    CompletionUseCase.stream()'s `_post_mask_active` gate is True.
    """

    def __init__(self, guardrail_configs: dict[str, Any]) -> None:
        self._guardrail_configs = guardrail_configs

    async def authenticate(self, raw_key: str | None) -> AuthzResult:
        return AuthzResult(
            tenant_id=TENANT_A,
            key_id=KEY_ID,
            guardrail_configs=self._guardrail_configs,
        )


def _make_masked_uc() -> CompletionUseCase:
    """CompletionUseCase wired with the REAL RegexGuardrailEvaluator + pii_mask=mask.

    Using the real evaluator (not a fake) means these tests exercise the actual
    per-choice _mask_pii_in_body masking behavior end-to-end, the same evaluator
    tests/guardrails/test_guardrails_core.py's S15 (non-streaming) uses.
    """
    return CompletionUseCase(
        _GuardrailAuthenticator({"pii_mask": {"enabled": True, "mode": "mask"}}),
        FakeModelChecker(),
        guardrail_evaluator=RegexGuardrailEvaluator(),
    )


async def _drain_unit(
    upstream: PlanStreamUpstream, recorder: FakeUsageRecorder
) -> list[bytes]:
    """Drive CompletionUseCase.stream() directly and collect every yielded chunk."""
    uc = _make_masked_uc()
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(PLAIN),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=None,
    )
    chunks: list[bytes] = []
    async for chunk in gen:
        chunks.append(chunk)
    await asyncio.sleep(0)
    return chunks


def _parse_choice_contents(raw: bytes) -> dict[int, str]:
    """Black-box SSE parser (independent of the implementation under test): assembles
    assistant text PER CHOICE INDEX from raw SSE bytes, for assertions only."""
    by_choice: dict[int, list[str]] = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload = stripped[len("data:") :].strip()
        if payload in ("[DONE]", ""):
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        choices = parsed.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            idx_raw = choice.get("index", 0)
            idx = idx_raw if isinstance(idx_raw, int) else 0
            holder = choice.get("delta") or choice.get("message") or {}
            content = holder.get("content") if isinstance(holder, dict) else None
            if isinstance(content, str) and content:
                by_choice.setdefault(idx, []).append(content)
    return {idx: "".join(parts) for idx, parts in by_choice.items()}


# n=2 choices, each with its OWN distinct email — provider format includes "index".
_CHOICE0_CHUNK = (
    b'data: {"id":"multi","choices":[{"index":0,"delta":'
    b'{"content":"Contact alice@example.com for details"}}]}\n\n'
)
_CHOICE1_CHUNK = (
    b'data: {"id":"multi","choices":[{"index":1,"delta":'
    b'{"content":"Contact bob@example.com instead"}}]}\n\n'
)
_MULTI_USAGE_CHUNK = b'data: {"usage":{"prompt_tokens":10,"completion_tokens":6,"total_tokens":16}}\n\n'
_MULTI_DONE_CHUNK = b"data: [DONE]\n\n"


async def test_streaming_post_call_pii_mask_n_choices_masked_independently() -> None:
    """HOLE 1: n=2 choices, pii_mask=mask → EACH choice masked independently.

    RED (hole present): choice 0 renders a corrupted blob merging BOTH choices' text
    (extract_content_from_sse concatenates every choice into one string before
    masking) and choice 1 renders EMPTY (the call-wide `assigned` flag blanks every
    content field after the first).
    GREEN (hole closed): choice 0 contains ONLY its own masked text, choice 1
    contains ONLY its own masked text — matching complete()'s _mask_pii_in_body
    contract of masking each choice independently.
    """
    upstream = PlanStreamUpstream(
        {PLAIN: [_CHOICE0_CHUNK, _CHOICE1_CHUNK, _MULTI_USAGE_CHUNK, _MULTI_DONE_CHUNK]}
    )
    recorder = FakeUsageRecorder()

    chunks = await _drain_unit(upstream, recorder)
    raw = b"".join(chunks)

    by_choice = _parse_choice_contents(raw)
    assert 0 in by_choice, f"choice 0 must still render content; got {by_choice!r}"
    assert 1 in by_choice, (
        f"HOLE 1: choice 1 must not be blanked to empty by a call-wide `assigned` "
        f"flag; got by_choice={by_choice!r}, raw={raw!r}"
    )

    text0 = by_choice[0]
    text1 = by_choice[1]

    assert text0, "choice 0 content must not be empty"
    assert text1, "HOLE 1: choice 1 content must not be empty"

    assert "[EMAIL_REDACTED]" in text0, f"choice 0 must be masked; got {text0!r}"
    assert "alice@example.com" not in text0, f"choice 0's own email must be masked; got {text0!r}"
    assert "bob@example.com" not in text0, (
        f"HOLE 1: choice 0 must NOT contain choice 1's raw text (cross-choice "
        f"corruption); got {text0!r}"
    )
    assert "instead" not in text0, (
        f"HOLE 1: choice 0 must NOT contain choice 1's text at all; got {text0!r}"
    )

    assert "[EMAIL_REDACTED]" in text1, f"choice 1 must be masked; got {text1!r}"
    assert "bob@example.com" not in text1, f"choice 1's own email must be masked; got {text1!r}"
    assert "alice@example.com" not in text1, (
        f"HOLE 1: choice 1 must NOT contain choice 0's raw text; got {text1!r}"
    )
    assert "Contact" in text1, f"choice 1 must retain its own surrounding text; got {text1!r}"


def test_rewrite_sse_content_fails_closed_on_malformed_frame() -> None:
    """Blocker 3 (HIGH): a malformed `data:` frame must NOT make _rewrite_sse_content
    fall back to emitting the ORIGINAL raw (unmasked) bytes.

    RED today: the whole body is wrapped in `except Exception: return chunks`, so a
    single unparseable frame anywhere in the buffered stream throws away the already-
    computed masked text and ships the raw PII-bearing byte stream verbatim — the wrong
    fail-direction for a masking control.
    GREEN: the unparseable frame is dropped (fail-closed — we cannot prove it PII-free),
    the parseable content frame is still masked, and no raw PII survives."""
    from gateway.proxy.application.use_cases import _rewrite_sse_content

    chunks = [
        b'data: {"choices":[{"index":0,"delta":{"content":"my ssn is 123-45-6789"}}]}\n\n',
        b"data: {this is not valid json\n\n",
        b"data: [DONE]\n\n",
    ]
    out = b"".join(_rewrite_sse_content(chunks, {0: "my ssn is [SSN_REDACTED]"}))
    text = out.decode("utf-8", "replace")
    assert "[SSN_REDACTED]" in text, f"masked text must still be emitted; got {text!r}"
    assert "123-45-6789" not in text, (
        f"raw PII must NEVER survive a malformed sibling frame; got {text!r}"
    )
    assert "this is not valid json" not in text, (
        f"the unparseable frame's raw bytes must be dropped, not passed through; got {text!r}"
    )


class _UnexpectedProviderError(Exception):
    """Simulates an adapter/provider exception type OTHER than UpstreamUnavailableError,
    CircuitOpenError, or UpstreamRateLimitedError — an unhandled-exception-type branch
    that pre-fix (masking-inactive) would have already streamed its prefix live."""


async def test_streaming_post_call_pii_mask_flushes_prefix_on_unexpected_exception() -> None:
    """HOLE 2: an unexpected exception mid-stream with masking active must still flush
    the buffered (masked) prefix to the client before the stream ends/errors.

    RED (hole present): _post_mask_active withholds all bytes until a flush point; the
    only flush points are BandwidthExhaustedError, (UpstreamUnavailableError,
    CircuitOpenError), and clean completion. `_UnexpectedProviderError` matches none of
    those, so it propagates straight out of `_wrapped()` with `collected` never flushed
    — the client receives ZERO bytes despite the upstream having already generated (and
    the gateway having already buffered) real content.
    GREEN (hole closed): a catch-all flushes the masked buffered prefix before
    re-raising, so the client still receives what was generated so far.
    """
    upstream = PlanStreamUpstream(
        {
            PLAIN: [
                b'data: {"id":"boom","choices":[{"delta":{"content":"Contact user@example.com now"}}]}\n\n',
                _UnexpectedProviderError("simulated unexpected adapter failure"),
            ]
        }
    )
    recorder = FakeUsageRecorder()
    uc = _make_masked_uc()
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(PLAIN),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=None,
    )

    chunks: list[bytes] = []
    with pytest.raises(_UnexpectedProviderError):
        async for chunk in gen:
            chunks.append(chunk)
    await asyncio.sleep(0)

    raw = b"".join(chunks)
    assert raw, (
        "HOLE 2: the buffered masked prefix must reach the client before the "
        "unexpected exception propagates — got an EMPTY stream (silent total loss)"
    )
    assert b"user@example.com" not in raw, f"leaked raw email in flushed prefix: {raw!r}"
    assert b"[EMAIL_REDACTED]" in raw, f"expected masked token in flushed prefix, got: {raw!r}"
