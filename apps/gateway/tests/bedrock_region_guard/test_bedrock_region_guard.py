"""Red suite for residency-bedrock-region-guard (M2) — frozen contract.

Fail-closed Bedrock BYOK region guard: a tenant-supplied AWS credential region
(``BedrockCredential.region``) must share the geo prefix of the resolved model
id's AWS cross-region-inference-profile prefix (``us.``/``eu.``/``apac.``), or
the request is refused BEFORE any Bedrock HTTP dial. The leak this closes is
PAYLOAD TRANSIT — an ``eu.``-profile request dialed at a US endpoint sends the
prompt to a US region even if AWS later rejects the invocation, so the guard
must run before ``_build_endpoint`` / the first ``httpx`` call, not rely on an
AWS ``ValidationException``.

Contract: FROZEN @ v1 (residency-bedrock-region-guard TASK.md §3).
  - ``_PROFILE_PREFIX_TO_GEO: dict[str, str] = {"us": "us", "eu": "eu", "apac": "ap"}``
    — fixed, internal, never tenant-supplied.
  - ``_assert_region_consistent(model_id, aws_region) -> None`` — the SINGLE shared
    helper called at the top of ``BedrockCompletionUpstream.complete``, ``.stream``,
    and ``BedrockEmbeddingsProvider.post_json``, AFTER credential + model_id
    resolution, BEFORE ``_build_endpoint`` / any httpx call.
  - No recognized prefix -> unconstrained (M3, byte-identical to today).
  - Recognized prefix + matching geo -> proceeds unchanged (M1).
  - Recognized prefix + mismatched/unclassifiable geo -> raises ``ProblemError``
    ``ERR_BEDROCK_REGION_MISMATCH`` (403), BEFORE any httpx call (R1/R2).

All HTTP behavior uses httpx.MockTransport with a call counter — no network, no
real AWS credentials. Mirrors the existing bedrock_provider / bedrock_streaming /
bedrock_embeddings test fixtures exactly (v25 task-3 contextvar-based credential).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from gateway.core.errors import ProblemError
from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.provider_credentials import BedrockCredential

# RED until BUILD creates these symbols.
from gateway.proxy.infrastructure.bedrock_embeddings import (  # noqa: E402
    BedrockEmbeddingsProvider,
)
from gateway.proxy.infrastructure.bedrock_embeddings import (  # noqa: E402
    _assert_region_consistent as _embeddings_assert_region_consistent,
)
from gateway.proxy.infrastructure.bedrock_upstream import (  # noqa: E402
    BedrockCompletionUpstream,
    _assert_region_consistent,
    _PROFILE_PREFIX_TO_GEO,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

_EU_PROFILE_MODEL = "eu.anthropic.claude-3-5-sonnet-20241022-v2:0"
_APAC_PROFILE_MODEL = "apac.anthropic.claude-3-5-sonnet-20241022-v2:0"
_US_PROFILE_MODEL = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
_UNPREFIXED_MODEL = "anthropic.claude-3-haiku-20240307-v1:0"
_TITAN_EMBED_MODEL = "amazon.titan-embed-text-v1"

_CONVERSE_200: dict[str, Any] = {
    "output": {"message": {"role": "assistant", "content": [{"text": "hi"}]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 3, "outputTokens": 1, "totalTokens": 4},
}

_TITAN_200: dict[str, Any] = {"embedding": [0.1, 0.2], "inputTextTokenCount": 2}


def _cred(region: str) -> BedrockCredential:
    return BedrockCredential(
        access_key_id="AKIDTEST000000000000",
        secret_access_key="fakesecretkey0000000000000000000000000000",
        region=region,
    )


def _counting_handler(
    response: dict[str, Any], hits: list[int], *, status: int = 200
) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        hits[0] += 1
        return httpx.Response(status, json=response)

    return handler


def _make_completion_adapter(handler: Any) -> BedrockCompletionUpstream:
    adapter = BedrockCompletionUpstream(  # type: ignore[call-arg]
        endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        default_max_tokens=4096,
        max_retries=0,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


def _make_embeddings_provider(handler: Any) -> BedrockEmbeddingsProvider:
    provider = BedrockEmbeddingsProvider(  # type: ignore[call-arg]
        endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com",
    )
    provider._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return provider


async def _drain(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in stream]


# ===========================================================================
# _assert_region_consistent — pure helper unit tests
# ===========================================================================


def test_helper_us_prefix_requires_us_geo() -> None:
    _assert_region_consistent("us.anthropic.claude-3-5-haiku-20241022-v1:0", "us-west-2")
    with pytest.raises(ProblemError) as exc:
        _assert_region_consistent("us.anthropic.claude-3-5-haiku-20241022-v1:0", "eu-west-1")
    assert exc.value.status == 403
    assert exc.value.code == "ERR_BEDROCK_REGION_MISMATCH"


def test_helper_eu_prefix_requires_eu_geo() -> None:
    _assert_region_consistent("eu.anthropic.claude-3-5-sonnet-20241022-v2:0", "eu-central-1")
    with pytest.raises(ProblemError):
        _assert_region_consistent("eu.anthropic.claude-3-5-sonnet-20241022-v2:0", "us-east-1")


def test_helper_apac_prefix_requires_ap_geo() -> None:
    _assert_region_consistent("apac.anthropic.claude-3-5-sonnet-20241022-v2:0", "ap-southeast-1")
    with pytest.raises(ProblemError):
        _assert_region_consistent("apac.anthropic.claude-3-5-sonnet-20241022-v2:0", "us-east-1")


def test_helper_unrecognized_prefix_is_unconstrained() -> None:
    """M3: an unprefixed/legacy id (or a prefix that is not us/eu/apac) never raises."""
    _assert_region_consistent("anthropic.claude-3-haiku-20240307-v1:0", "us-east-1")
    _assert_region_consistent("anthropic.claude-3-haiku-20240307-v1:0", "not-a-region")
    _assert_region_consistent("amazon.titan-embed-text-v1", "eu-central-1")


def test_helper_malformed_region_fails_closed_under_geo_prefixed_model() -> None:
    """R2: an unclassifiable credential region under a geo-prefixed model fails CLOSED."""
    with pytest.raises(ProblemError) as exc:
        _assert_region_consistent("eu.anthropic.claude-3-5-sonnet-20241022-v2:0", "not-a-region")
    assert exc.value.status == 403
    assert exc.value.code == "ERR_BEDROCK_REGION_MISMATCH"


def test_helper_prefix_map_pins_the_three_known_geos() -> None:
    """Guards the §1 ⚠ assumption: extend the map, don't hand-roll a new predicate."""
    assert _PROFILE_PREFIX_TO_GEO == {"us": "us", "eu": "eu", "apac": "ap"}


def test_helper_error_message_names_no_secret() -> None:
    """The mismatch message names the required/actual geo only — never a secret."""
    with pytest.raises(ProblemError) as exc:
        _assert_region_consistent("eu.anthropic.claude-3-5-sonnet-20241022-v2:0", "us-east-1")
    text = f"{exc.value.title} {exc.value.detail or ''}"
    assert "secret" not in text.lower()
    assert "AKID" not in text
    assert "fakesecretkey" not in text


# ===========================================================================
# M2 — the shared guard is the single source of truth for all three entry points
# ===========================================================================


def test_M2_all_three_entry_points_share_the_same_helper() -> None:
    assert _embeddings_assert_region_consistent is _assert_region_consistent


# ===========================================================================
# R1 — eu-profile with a us credential is refused before any dial
# ===========================================================================


async def test_R1_complete_refused_before_dial() -> None:
    hits = [0]
    adapter = _make_completion_adapter(_counting_handler(_CONVERSE_200, hits))
    tok = set_provider_credential(_cred("us-east-1"))
    try:
        with pytest.raises(ProblemError) as exc:
            await adapter.complete({"model": _EU_PROFILE_MODEL, "messages": []})
    finally:
        reset_provider_credential(tok)
    assert exc.value.status == 403
    assert exc.value.code == "ERR_BEDROCK_REGION_MISMATCH"
    assert hits[0] == 0, "no httpx call — the prompt must never transit the wrong region"


async def test_R1_stream_refused_before_dial_without_iterating() -> None:
    """The guard must fire synchronously — before the generator is even iterated —
    mirroring VertexCompletionUpstream.stream's fail-closed-before-return pattern.
    A ``pytest.raises`` around the bare `.stream(...)` call (no `async for`, no
    `_drain`) is the proof: nothing runs before the exception is raised.
    """
    hits = [0]
    adapter = _make_completion_adapter(_counting_handler(_CONVERSE_200, hits))
    tok = set_provider_credential(_cred("us-east-1"))
    try:
        with pytest.raises(ProblemError) as exc:
            adapter.stream({"model": _EU_PROFILE_MODEL, "messages": []})
    finally:
        reset_provider_credential(tok)
    assert exc.value.status == 403
    assert exc.value.code == "ERR_BEDROCK_REGION_MISMATCH"
    assert hits[0] == 0, "no httpx call — the prompt must never transit the wrong region"


async def test_R1_embeddings_refused_before_dial() -> None:
    hits = [0]
    provider = _make_embeddings_provider(_counting_handler(_TITAN_200, hits))
    tok = set_provider_credential(_cred("us-east-1"))
    try:
        with pytest.raises(ProblemError) as exc:
            await provider.post_json("", {"model": _EU_PROFILE_MODEL, "input": "hello"})
    finally:
        reset_provider_credential(tok)
    assert exc.value.status == 403
    assert exc.value.code == "ERR_BEDROCK_REGION_MISMATCH"
    assert hits[0] == 0, "no httpx call — the prompt must never transit the wrong region"


# ===========================================================================
# M1 — matching geo proceeds unchanged
# ===========================================================================


async def test_M1_apac_profile_with_ap_credential_proceeds() -> None:
    hits = [0]
    adapter = _make_completion_adapter(_counting_handler(_CONVERSE_200, hits))
    tok = set_provider_credential(_cred("ap-southeast-1"))
    try:
        status, body = await adapter.complete({"model": _APAC_PROFILE_MODEL, "messages": []})
    finally:
        reset_provider_credential(tok)
    assert status == 200
    assert body["choices"][0]["message"]["content"] == "hi"
    assert hits[0] == 1


async def test_M1_us_profile_with_us_west_credential_proceeds() -> None:
    """Any us-* region satisfies the us. geo — not just us-east-1."""
    hits = [0]
    adapter = _make_completion_adapter(_counting_handler(_CONVERSE_200, hits))
    tok = set_provider_credential(_cred("us-west-2"))
    try:
        status, _body = await adapter.complete({"model": _US_PROFILE_MODEL, "messages": []})
    finally:
        reset_provider_credential(tok)
    assert status == 200
    assert hits[0] == 1


async def test_M1_stream_matching_geo_proceeds() -> None:
    hits = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        hits[0] += 1
        return httpx.Response(200, content=b"", headers={"content-type": "application/vnd.amazon.eventstream"})

    adapter = _make_completion_adapter(handler)
    tok = set_provider_credential(_cred("eu-central-1"))
    try:
        await _drain(adapter.stream({"model": _EU_PROFILE_MODEL, "messages": []}))
    finally:
        reset_provider_credential(tok)
    assert hits[0] == 1


async def test_M1_embeddings_matching_geo_proceeds() -> None:
    hits = [0]
    provider = _make_embeddings_provider(_counting_handler(_TITAN_200, hits))
    tok = set_provider_credential(_cred("us-east-1"))
    try:
        status, body = await provider.post_json(
            "", {"model": "us.amazon.titan-embed-text-v2:0", "input": "hello"}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200
    assert hits[0] == 1
    assert body["data"][0]["embedding"] == [0.1, 0.2]


# ===========================================================================
# M3 — an unprefixed legacy model id is unaffected
# ===========================================================================


async def test_M3_unprefixed_model_unaffected_on_complete() -> None:
    hits = [0]
    adapter = _make_completion_adapter(_counting_handler(_CONVERSE_200, hits))
    tok = set_provider_credential(_cred("us-east-1"))
    try:
        status, _body = await adapter.complete({"model": _UNPREFIXED_MODEL, "messages": []})
    finally:
        reset_provider_credential(tok)
    assert status == 200
    assert hits[0] == 1


async def test_M3_unprefixed_model_unaffected_regardless_of_region() -> None:
    """A legacy id imposes no constraint even against a region with NO geo match."""
    hits = [0]
    adapter = _make_completion_adapter(_counting_handler(_CONVERSE_200, hits))
    tok = set_provider_credential(_cred("sa-east-1"))
    try:
        status, _body = await adapter.complete({"model": _UNPREFIXED_MODEL, "messages": []})
    finally:
        reset_provider_credential(tok)
    assert status == 200
    assert hits[0] == 1


async def test_M3_unprefixed_model_unaffected_on_embeddings() -> None:
    hits = [0]
    provider = _make_embeddings_provider(_counting_handler(_TITAN_200, hits))
    tok = set_provider_credential(_cred("sa-east-1"))
    try:
        status, _body = await provider.post_json("", {"model": _TITAN_EMBED_MODEL, "input": "x"})
    finally:
        reset_provider_credential(tok)
    assert status == 200
    assert hits[0] == 1


# ===========================================================================
# R2 — a malformed/unclassifiable credential region fails closed
# ===========================================================================


async def test_R2_malformed_region_fails_closed_on_complete() -> None:
    hits = [0]
    adapter = _make_completion_adapter(_counting_handler(_CONVERSE_200, hits))
    tok = set_provider_credential(_cred("not-a-region"))
    try:
        with pytest.raises(ProblemError) as exc:
            await adapter.complete({"model": _EU_PROFILE_MODEL, "messages": []})
    finally:
        reset_provider_credential(tok)
    assert exc.value.status == 403
    assert exc.value.code == "ERR_BEDROCK_REGION_MISMATCH"
    assert hits[0] == 0


async def test_R2_malformed_region_fails_closed_on_stream() -> None:
    hits = [0]
    adapter = _make_completion_adapter(_counting_handler(_CONVERSE_200, hits))
    tok = set_provider_credential(_cred("not-a-region"))
    try:
        with pytest.raises(ProblemError) as exc:
            adapter.stream({"model": _EU_PROFILE_MODEL, "messages": []})
    finally:
        reset_provider_credential(tok)
    assert exc.value.code == "ERR_BEDROCK_REGION_MISMATCH"
    assert hits[0] == 0


async def test_R2_malformed_region_fails_closed_on_embeddings() -> None:
    hits = [0]
    provider = _make_embeddings_provider(_counting_handler(_TITAN_200, hits))
    tok = set_provider_credential(_cred("not-a-region"))
    try:
        with pytest.raises(ProblemError) as exc:
            await provider.post_json("", {"model": _EU_PROFILE_MODEL, "input": "hello"})
    finally:
        reset_provider_credential(tok)
    assert exc.value.code == "ERR_BEDROCK_REGION_MISMATCH"
    assert hits[0] == 0
