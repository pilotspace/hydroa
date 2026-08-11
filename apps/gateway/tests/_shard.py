"""Deterministic FILE-level test sharding, for splitting the suite across CI matrix jobs.

Why this exists (todo #96): the gateway suite took 65-82 minutes on one ubuntu-latest
runner and `-n 4` bought almost nothing, because that runner's 4 vCPUs are shared with the
Postgres and Redis service containers and every worker carries a ~1.92x coverage multiplier
while contending on one database. Adding workers to one box is a dead end; adding BOXES is
the lever. Each matrix shard is its own runner with its own Postgres and Redis.

Design decisions, each load-bearing:

* Splits by FILE, never by individual test. `--dist loadscope` already keeps a module on one
  xdist worker because suites carry module-level state, and several suites share a Postgres
  schema (see [[gateway-suites-xdist-schema-collision]]). Splitting mid-file would break both.
* Balances by test COUNT using longest-processing-time-first, not by hashing the path. Hashing
  is one line but gives no balance guarantee, and a shard that runs twice as long as its
  siblings wastes exactly the wall-clock this exists to reclaim. Count is a fair proxy here
  because per-test cost is dominated by fixture setup, which todo #100 measured as roughly
  uniform (0.43-1.20s setup, with ZERO test bodies clearing 50ms).
* Fully deterministic: files are ordered by (descending test count, path), so the same
  collection always produces the same partition. Two shards can never both claim a file and
  none can be silently dropped.
* Reports what it dropped through `pytest_deselected`, so the summary line shows
  "N deselected" rather than a quietly smaller number. A sharding bug that loses tests would
  otherwise look exactly like a green run — the masked-gate failure mode.
* Fails LOUDLY on an empty shard. A shard with no tests is a misconfiguration (more shards
  than files, or a bad index), and reporting green for it would be the worst outcome.

Inert unless BOTH env vars are set, so local `pytest` and `make test` are untouched.

Usage:
    PYTEST_SHARDS=6 PYTEST_SHARD=3 pytest -p tests._shard ...
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pytest

_ENV_TOTAL = "PYTEST_SHARDS"
_ENV_INDEX = "PYTEST_SHARD"


class ShardConfigError(Exception):
    """Raised when the shard env vars are present but not usable."""


def _shard_config() -> tuple[int, int] | None:
    """Return (index, total) 1-based, or None when sharding is off.

    Raises rather than silently disabling itself when the vars are present but wrong: a
    typo'd PYTEST_SHARD that quietly ran the WHOLE suite on every shard would turn a 6x
    speedup into a 6x cost with no signal, and one that quietly ran nothing would report
    green having tested nothing.
    """
    raw_total = os.environ.get(_ENV_TOTAL, "").strip()
    raw_index = os.environ.get(_ENV_INDEX, "").strip()
    if not raw_total and not raw_index:
        return None
    if not raw_total or not raw_index:
        raise ShardConfigError(
            f"sharding needs BOTH {_ENV_TOTAL} and {_ENV_INDEX}; "
            f"got {_ENV_TOTAL}={raw_total!r} {_ENV_INDEX}={raw_index!r}"
        )
    try:
        total, index = int(raw_total), int(raw_index)
    except ValueError as exc:
        raise ShardConfigError(
            f"{_ENV_TOTAL}/{_ENV_INDEX} must be integers; got {raw_total!r}/{raw_index!r}"
        ) from exc
    if total < 1:
        raise ShardConfigError(f"{_ENV_TOTAL} must be >= 1; got {total}")
    if not 1 <= index <= total:
        raise ShardConfigError(f"{_ENV_INDEX} must be in 1..{total}; got {index}")
    return index, total


def _partition(items: list[Any], total: int) -> dict[str, int]:
    """Map each test file to a shard index (0-based) balanced by test count.

    Longest-processing-time-first greedy: assign the heaviest remaining file to the
    currently-lightest shard. Ties broken by path, then by lowest shard index, so the
    result depends only on the collected set.
    """
    by_file: dict[str, int] = defaultdict(int)
    for item in items:
        by_file[item.location[0]] += 1

    heaviest_first = sorted(by_file, key=lambda path: (-by_file[path], path))
    load = [0] * total
    assignment: dict[str, int] = {}
    for path in heaviest_first:
        target = min(range(total), key=lambda i: (load[i], i))
        assignment[path] = target
        load[target] += by_file[path]
    return assignment


def pytest_report_header(config: pytest.Config) -> str | None:
    """State the shard in the run header, so a log is self-describing."""
    cfg = _shard_config()
    if cfg is None:
        return None
    index, total = cfg
    return f"shard: {index}/{total} (file-level, balanced by test count)"


def pytest_collection_modifyitems(config: pytest.Config, items: list[Any]) -> None:
    cfg = _shard_config()
    if cfg is None:
        return
    index, total = cfg
    if total == 1:
        return

    assignment = _partition(items, total)
    mine = index - 1
    kept = [i for i in items if assignment[i.location[0]] == mine]
    dropped = [i for i in items if assignment[i.location[0]] != mine]

    if not kept:
        raise ShardConfigError(
            f"shard {index}/{total} collected ZERO tests from {len(items)} items across "
            f"{len(assignment)} files — almost certainly more shards than files. Refusing "
            f"to report a green run for an empty shard."
        )

    items[:] = kept
    if dropped:
        config.hook.pytest_deselected(items=dropped)
