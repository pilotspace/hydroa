"""v33 passthrough-nonfinite-sanitize — the three sibling JSONResponse render sites
must null-replace a non-finite upstream float instead of 500ing on serialization.

Mirrors the v28 stt-nonfinite-passthrough behavior at images / embeddings / chat-non-stream.
Real Postgres:5433 + Redis. RED before build: tests 1–3 get a 500 (allow_nan render) until
the router sanitizes; test 4 (all-finite) passes pre-build as the no-regression guard.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    CHAT_MODEL_ID,
    EMBED_MODEL_ID,
    IMAGE_MODEL_ID,
    FakeCompletionUpstream,
    FakeUpstreamProvider,
    auth_key,
    inject_fake_openai_provider,
    seed_chat_model,
    seed_embedding_model,
    seed_image_model,
)

pytestmark = pytest.mark.asyncio

IMAGES = "/v1/images/generations"
EMBEDDINGS = "/v1/embeddings"
COMPLETIONS = "/v1/chat/completions"


async def test_images_nonfinite_body_sanitized_to_null(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    await seed_image_model(db_session)
    fake = FakeUpstreamProvider(name="openai")
    fake.set_post_json_response(
        200,
        {"created": 1, "data": [{"url": "https://x/i.png", "score": float("nan")}]},
    )
    inject_fake_openai_provider(app, fake)

    resp = await client.post(
        IMAGES,
        json={"model": IMAGE_MODEL_ID, "prompt": "a cat"},
        headers=auth_key(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"][0]["score"] is None


async def test_embeddings_nonfinite_body_sanitized_to_null(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    await seed_embedding_model(db_session)
    fake = FakeUpstreamProvider(name="openai")
    fake.set_post_json_response(
        200,
        {
            "object": "list",
            "model": EMBED_MODEL_ID,
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1, float("inf"), 0.3]}],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        },
    )
    inject_fake_openai_provider(app, fake)

    resp = await client.post(
        EMBEDDINGS,
        json={"model": EMBED_MODEL_ID, "input": "hello"},
        headers=auth_key(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    embedding = resp.json()["data"][0]["embedding"]
    assert embedding[1] is None
    assert embedding[0] == 0.1 and embedding[2] == 0.3  # finite values preserved
    assert fake.post_json_calls, "upstream was called — the billing path ran"


async def test_chat_nonstream_nonfinite_body_sanitized_to_null(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    await seed_chat_model(db_session)
    app.state.completion_upstream = FakeCompletionUpstream(
        body={
            "id": "gen-nf-1",
            "object": "chat.completion",
            "model": CHAT_MODEL_ID,
            "choices": [
                {
                    "message": {"role": "assistant", "content": "hi"},
                    "logprobs": {"content": [{"token": "hi", "logprob": float("-inf")}]},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )

    resp = await client.post(
        COMPLETIONS,
        json={
            "model": CHAT_MODEL_ID,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
        headers=auth_key(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["choices"][0]["logprobs"]["content"][0]["logprob"] is None


async def test_images_all_finite_body_unchanged(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """No-regression guard: an all-finite body is returned unchanged."""
    await seed_image_model(db_session)
    body = {"created": 1, "data": [{"url": "https://x/i.png", "revised_prompt": "a cat"}]}
    fake = FakeUpstreamProvider(name="openai")
    fake.set_post_json_response(200, body)
    inject_fake_openai_provider(app, fake)

    resp = await client.post(
        IMAGES,
        json={"model": IMAGE_MODEL_ID, "prompt": "a cat"},
        headers=auth_key(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == body
