"""RED suite — resolve_cache_ttl (per-request TTL override, v19 task 4 §3).

PURE · TOTAL · never raises. Fails with a clear RED message until
response_cache.resolve_cache_ttl is built.
"""

from __future__ import annotations

from typing import Any

import pytest

try:
    from gateway.proxy.infrastructure.response_cache import resolve_cache_ttl  # noqa: F401

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def _resolve(headers: Any, default_ttl: int = 300, max_ttl: int = 86400) -> int:
    if not _AVAILABLE:
        pytest.fail("RED: response_cache.resolve_cache_ttl not yet implemented — build pending")
    from gateway.proxy.infrastructure.response_cache import resolve_cache_ttl

    return resolve_cache_ttl(headers, default_ttl, max_ttl)


def test_max_age_overrides() -> None:
    assert _resolve({"cache-control": "max-age=60"}) == 60


def test_max_age_clamps_to_cap() -> None:
    assert _resolve({"cache-control": "max-age=999999"}) == 86400


def test_absent_defaults() -> None:
    assert _resolve({}) == 300
    assert _resolve(None) == 300


def test_invalid_max_age_defaults() -> None:
    assert _resolve({"cache-control": "max-age=abc"}) == 300
    assert _resolve({"cache-control": "max-age=-5"}) == 300
    assert _resolve({"cache-control": "max-age=0"}) == 300
    assert _resolve({"cache-control": "no-store"}) == 300


def test_max_age_among_directives() -> None:
    assert _resolve({"cache-control": "no-store, max-age=120"}) == 120
    assert _resolve({"cache-control": "MAX-AGE=45"}) == 45


def test_never_raises() -> None:
    odd: list[Any] = [
        None,
        {},
        {"cache-control": ""},
        {"cache-control": "max-age="},
        {"cache-control": "max-age=1.5"},
        {"cache-control": 123},
        {"other": "x"},
    ]
    for h in odd:
        # Must not raise; bad/absent → default.
        assert _resolve(h) == 300
