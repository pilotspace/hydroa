"""RED suite — build_embedding_cache_key (v19 task 4 §3).

Exact-match key over embedding output-affecting fields, per-tenant, distinct prefix.
Fails RED until response_cache.build_embedding_cache_key is built.
"""

from __future__ import annotations

from typing import Any

import pytest

try:
    from gateway.proxy.infrastructure.response_cache import (  # noqa: F401
        build_embedding_cache_key,
    )

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

TENANT_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _key(tenant: str, payload: dict[str, Any]) -> str:
    if not _AVAILABLE:
        pytest.fail(
            "RED: response_cache.build_embedding_cache_key not yet implemented — build pending"
        )
    from gateway.proxy.infrastructure.response_cache import build_embedding_cache_key

    return build_embedding_cache_key(tenant, payload)


def test_same_inputs_same_key() -> None:
    p1 = {"model": "m", "input": "hi", "dimensions": 256, "encoding_format": "float"}
    p2 = {"encoding_format": "float", "dimensions": 256, "input": "hi", "model": "m"}
    assert _key(TENANT_A, p1) == _key(TENANT_A, p2)


def test_prefix_is_embed_cache() -> None:
    assert _key(TENANT_A, {"model": "m", "input": "hi"}).startswith(f"embed-cache:{TENANT_A}:")


def test_differs_from_chat_key() -> None:
    from gateway.proxy.infrastructure.response_cache import build_cache_key

    payload = {"model": "m", "input": "hi"}
    assert _key(TENANT_A, payload) != build_cache_key(TENANT_A, payload)


def test_input_change_changes_key() -> None:
    assert _key(TENANT_A, {"model": "m", "input": "a"}) != _key(
        TENANT_A, {"model": "m", "input": "b"}
    )


def test_model_change_changes_key() -> None:
    assert _key(TENANT_A, {"model": "m1", "input": "a"}) != _key(
        TENANT_A, {"model": "m2", "input": "a"}
    )


def test_dimensions_change_changes_key() -> None:
    p1 = {"model": "m", "input": "a", "dimensions": 256}
    p2 = {"model": "m", "input": "a", "dimensions": 512}
    assert _key(TENANT_A, p1) != _key(TENANT_A, p2)


def test_absent_field_excluded_not_null() -> None:
    # Omitting dimensions must NOT equal dimensions=None (absent fields excluded).
    p_absent = {"model": "m", "input": "a"}
    p_null = {"model": "m", "input": "a", "dimensions": None}
    assert _key(TENANT_A, p_absent) != _key(TENANT_A, p_null)


def test_tenant_isolation_in_key() -> None:
    payload = {"model": "m", "input": "a"}
    assert _key(TENANT_A, payload) != _key(TENANT_B, payload)
