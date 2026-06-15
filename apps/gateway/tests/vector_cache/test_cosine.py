"""RED suite — cosine_similarity pure function (v19 task 5 §3, VC11).

cosine is TOTAL: it never raises and never returns NaN/inf. Length mismatch or a
zero-norm vector returns 0.0 (treated as "no similarity"). Fails RED until
proxy/infrastructure/vector_cache.py is built.
"""

from __future__ import annotations

import pytest

try:
    from gateway.proxy.infrastructure.vector_cache import cosine_similarity

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def _cos(a: list[float], b: list[float]) -> float:
    if not _AVAILABLE:
        pytest.fail("RED: cosine_similarity not yet implemented — build pending")
    return cosine_similarity(a, b)


def test_cosine_identical_is_one() -> None:
    assert _cos([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero() -> None:
    assert _cos([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_is_negative_one() -> None:
    assert _cos([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_scale_invariant() -> None:
    # cosine ignores magnitude: a parallel vector of any positive scale is 1.0
    assert _cos([1.0, 1.0, 1.0], [3.0, 3.0, 3.0]) == pytest.approx(1.0)


def test_cosine_length_mismatch_returns_zero() -> None:
    assert _cos([1.0, 2.0, 3.0], [1.0, 2.0]) == 0.0  # total, never raises


def test_cosine_zero_vector_returns_zero() -> None:
    assert _cos([0.0, 0.0, 0.0], [1.0, 2.0, 3.0]) == 0.0  # zero norm → 0.0, never div-by-zero


def test_cosine_both_empty_returns_zero() -> None:
    assert _cos([], []) == 0.0
