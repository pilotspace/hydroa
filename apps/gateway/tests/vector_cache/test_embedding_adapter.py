"""Unit suite — build_embedding_adapter (v19 task 5, deps.py).

The embedder the vector cache uses to vectorize prompts: it resolves the embed model's
provider via its OWN short-lived session (NOT the request session — so the fire-and-forget
store path works after the response) and calls the embedding upstream. Returns None on
unknown model / non-200 / bad shape. These paths were previously uncovered (refute-read gap).
"""

from __future__ import annotations

from typing import Any

import pytest

from gateway.proxy.api import deps


class _FakeRow:
    def __init__(self, modality: str, provider: str) -> None:
        self.modality = modality
        self.provider = provider


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def one_or_none(self) -> Any:
        return self._row


class _FakeSession:
    """Async-context-manager session whose execute() returns a canned row."""

    def __init__(self, row: Any) -> None:
        self._row = row
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _FakeSession:
        self.entered = True
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        self.exited = True
        return False

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self._row)


class _FakeAdapter:
    def __init__(self, status: int, resp: dict[str, Any]) -> None:
        self._status = status
        self._resp = resp
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def post_json(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append((path, body))
        return self._status, self._resp


def _factory(session: _FakeSession) -> Any:
    def _make() -> _FakeSession:
        return session

    return _make


async def test_embed_returns_vector_and_uses_own_session(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _FakeAdapter(200, {"data": [{"embedding": [0.1, 0.2, 0.3]}]})
    monkeypatch.setattr(deps, "select_provider", lambda _m, _p, _r: adapter)
    session = _FakeSession(_FakeRow("embedding", "openai"))
    embed = deps.build_embedding_adapter(
        session_factory=_factory(session), registry=object(), embed_model="emb-1"
    )

    vec = await embed("the capital of france")
    assert vec == [0.1, 0.2, 0.3]
    assert adapter.calls == [("/embeddings", {"model": "emb-1", "input": "the capital of france"})]
    # The adapter opened AND closed its OWN session (the store-after-response reliability fix).
    assert session.entered and session.exited


async def test_embed_unknown_model_returns_none_without_calling_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[int] = []
    monkeypatch.setattr(deps, "select_provider", lambda *_a: called.append(1))  # type: ignore[arg-type]
    embed = deps.build_embedding_adapter(
        session_factory=_factory(_FakeSession(None)), registry=object(), embed_model="missing"
    )
    assert await embed("hi") is None
    assert called == []  # provider never resolved for an unknown/inactive model


async def test_embed_non_200_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _FakeAdapter(503, {})
    monkeypatch.setattr(deps, "select_provider", lambda *_a: adapter)
    embed = deps.build_embedding_adapter(
        session_factory=_factory(_FakeSession(_FakeRow("embedding", "openai"))),
        registry=object(),
        embed_model="emb-1",
    )
    assert await embed("hi") is None


async def test_embed_bad_shape_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # data is a dict, not a list → None (the exact regression the refute-read flagged).
    adapter = _FakeAdapter(200, {"data": {"embedding": [1.0]}})
    monkeypatch.setattr(deps, "select_provider", lambda *_a: adapter)
    embed = deps.build_embedding_adapter(
        session_factory=_factory(_FakeSession(_FakeRow("embedding", "openai"))),
        registry=object(),
        embed_model="emb-1",
    )
    assert await embed("hi") is None


async def test_embed_empty_data_list_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _FakeAdapter(200, {"data": []})
    monkeypatch.setattr(deps, "select_provider", lambda *_a: adapter)
    embed = deps.build_embedding_adapter(
        session_factory=_factory(_FakeSession(_FakeRow("embedding", "openai"))),
        registry=object(),
        embed_model="emb-1",
    )
    assert await embed("hi") is None
