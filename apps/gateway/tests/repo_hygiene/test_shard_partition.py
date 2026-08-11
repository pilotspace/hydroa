"""Standing guard on the CI shard partition (tests/_shard.py).

The property that matters is not "sharding runs" but "sharding loses nothing". A partition bug
drops tests, and a shard that tested less than it should still exits 0 — the masked-gate
failure mode this repo has already been bitten by (see the dashboard lint gate, todo #107, and
the migration parity gate that sat unreached for months). So the completeness property is
asserted here rather than trusted.

Verified against the real suite when the sharding landed: at N=6 the union of all six shards
was EXACTLY the 4578 collected tests, 0 duplicates, 0 missing, with 744-768 tests per shard.
These tests re-assert that on synthetic input so they cost milliseconds and cannot rot.
"""

from __future__ import annotations

import pytest

from tests import _shard


class _FakeItem:
    """Minimal stand-in for a pytest Item: only `.location[0]` is read by _partition."""

    def __init__(self, path: str) -> None:
        self.location = (path, 0, "")


def _items(spec: dict[str, int]) -> list[_FakeItem]:
    return [_FakeItem(path) for path, n in spec.items() for _ in range(n)]


def _files(spec: dict[str, int]) -> set[str]:
    return set(spec)


@pytest.mark.parametrize("total", [1, 2, 3, 4, 6, 7, 12])
def test_partition_assigns_every_file_exactly_once(total: int) -> None:
    """No file may be unassigned (tests vanish) or double-assigned (tests run twice)."""
    spec = {f"tests/suite_{i}/test_{i}.py": (i % 17) + 1 for i in range(60)}
    assignment = _shard._partition(_items(spec), total)

    assert set(assignment) == _files(spec), "every collected file must be assigned"
    assert all(0 <= v < total for v in assignment.values()), "shard index out of range"


@pytest.mark.parametrize("total", [2, 3, 4, 6, 7])
def test_partition_is_a_true_partition_of_the_test_count(total: int) -> None:
    """Summing the shards must reproduce the full test count — the completeness property."""
    spec = {f"tests/suite_{i}/test_{i}.py": (i * 7 % 23) + 1 for i in range(80)}
    items = _items(spec)
    assignment = _shard._partition(items, total)

    per_shard = [0] * total
    for item in items:
        per_shard[assignment[item.location[0]]] += 1

    assert sum(per_shard) == len(items), "shards must sum to the whole suite"
    assert all(n > 0 for n in per_shard), f"every shard must get work; got {per_shard}"


def test_partition_is_deterministic() -> None:
    """Same collection, same partition — otherwise a retry could shard differently."""
    spec = {f"tests/suite_{i}/test_{i}.py": (i % 11) + 1 for i in range(40)}
    first = _shard._partition(_items(spec), 5)
    second = _shard._partition(_items(spec), 5)
    assert first == second


def test_partition_balances_within_a_sane_margin() -> None:
    """LPT should keep shards close; a lopsided split wastes the wall-clock this reclaims.

    Not asserting a tight bound (that would be testing the scheduler, not the contract) —
    only that the heaviest shard is not pathologically worse than the lightest, which is what
    a hash-based split would have permitted.
    """
    spec = {f"tests/suite_{i}/test_{i}.py": (i % 29) + 1 for i in range(120)}
    items = _items(spec)
    assignment = _shard._partition(items, 6)

    per_shard = [0] * 6
    for item in items:
        per_shard[assignment[item.location[0]]] += 1

    assert max(per_shard) <= min(per_shard) * 1.25, f"partition too lopsided: {per_shard}"


def test_env_config_is_inert_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local `pytest` and `make test` must be completely unaffected."""
    monkeypatch.delenv("PYTEST_SHARDS", raising=False)
    monkeypatch.delenv("PYTEST_SHARD", raising=False)
    assert _shard._shard_config() is None


@pytest.mark.parametrize(
    ("total", "index"),
    [("6", ""), ("", "3"), ("6", "0"), ("6", "7"), ("0", "1"), ("six", "1"), ("6", "two")],
)
def test_broken_shard_config_raises_instead_of_silently_disabling(
    monkeypatch: pytest.MonkeyPatch, total: str, index: str
) -> None:
    """A typo must fail loudly.

    Silently disabling would run the WHOLE suite on every shard — a 6x cost with no signal.
    Silently selecting nothing would report green having tested nothing. Both are worse than
    a crash, so `_shard_config` raises.
    """
    monkeypatch.setenv("PYTEST_SHARDS", total)
    monkeypatch.setenv("PYTEST_SHARD", index)
    with pytest.raises(_shard.ShardConfigError):
        _shard._shard_config()
