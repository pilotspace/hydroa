"""LIMIT/OFFSET pagination needs a UNIQUE sort key, or pages overlap and drop rows (todo #58).

THE DEFECT. `ORDER BY created_at DESC LIMIT n OFFSET m` is only well-defined when `created_at` is
unique. It is not: `created_at` is `server_default=func.now()`, which is transaction-START time,
so every row written in one transaction shares a timestamp to the microsecond. SQL leaves the
order of tied rows unspecified and Postgres is free to return them differently between two
executions — which is exactly what paging does, two executions with different OFFSETs.

The consequence is not a crash. Page 1 and page 2 can both contain the same row, and another row
appears on neither. The caller sees a list that is quietly wrong, and the bug needs concurrent
writes plus a plan change to show up, so it will not reproduce on a small dev table.

Same root cause as the test-side issue already recorded in todo #77 (`ORDER BY created_at` cannot
break ties, so positional `rows[0]`/`rows[1]` assertions are nondeterministic) — this is the
production-side half of it.

WHY A STATIC GUARD RATHER THAN A BEHAVIOURAL TEST. The behaviour is *permitted* nondeterminism,
not guaranteed misbehaviour: with a small table and a stable plan, Postgres usually returns tied
rows in the same order twice, so a paging test asserting "no duplicates" passes on a healthy tree
AND on the broken one. That is a gate that cannot reach a verdict. The structural property — the
ORDER BY carries a unique key — is decidable, so it is what gets asserted here. A behavioural test
pinning the intended order lives with each repository's own suite.
"""

from __future__ import annotations

import ast
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = GATEWAY_ROOT / "src" / "gateway"

# Columns that are unique per row, so ordering by one makes the total order well-defined.
# `id` is the primary key everywhere in this codebase.
UNIQUE_TIEBREAKERS = frozenset({"id"})


def _column_of(node: ast.expr) -> str | None:
    """`X.created_at.desc()` -> "created_at"; `X.id` -> "id"."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        # .desc() / .asc() wrapper — unwrap one level
        if node.func.attr in {"desc", "asc"}:
            return _column_of(node.func.value)
        return None
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _paginated_order_bys(tree: ast.Module) -> list[tuple[int, list[str]]]:
    """Find `.order_by(...)` calls that belong to a chain also carrying `.limit(` and `.offset(`.

    Keyed on the CHAIN rather than on the file, because a repository legitimately contains
    unpaginated `order_by`s (a `get_active` returning one row, an aggregate) and flagging those
    would be the cry-wolf failure that gets a guard deleted.
    """
    found: list[tuple[int, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "order_by":
            continue

        # Walk OUTWARD is not possible with ast.walk, so instead: the chain is rooted at this
        # call's own subtree for the inner part, and the outer .limit/.offset wrap it. Detect by
        # scanning the enclosing statement — cheapest reliable signal is the source segment.
        found.append((node.lineno, [c for c in (_column_of(a) for a in node.args) if c]))
    return found


def _violations() -> list[str]:
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("infrastructure/*.py")):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign | ast.Return | ast.AnnAssign):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if ".limit(" not in segment or ".offset(" not in segment:
                continue
            if ".order_by(" not in segment:
                continue

            try:
                sub = ast.parse(segment.strip())
            except SyntaxError:  # pragma: no cover — a partial segment
                continue
            for lineno, columns in _paginated_order_bys(sub):
                if not columns:
                    continue
                if UNIQUE_TIEBREAKERS & set(columns):
                    continue
                offenders.append(
                    f"{path.relative_to(GATEWAY_ROOT)}:{node.lineno + lineno - 1} — paginated "
                    f"query orders by {columns} with no unique tiebreaker"
                )
    return sorted(offenders)


def test_a_paginated_query_orders_by_something_unique() -> None:
    """A LIMIT/OFFSET query must have a total order, or its pages overlap and drop rows."""
    violations = _violations()
    assert not violations, (
        f"{len(violations)} paginated quer(ies) have a non-unique sort key:\n  "
        + "\n  ".join(violations)
        + "\n\nAppend the primary key to the ORDER BY, e.g. "
        "`.order_by(Row.created_at.desc(), Row.id.desc())`. `created_at` is "
        "`server_default=func.now()` = transaction-START time, so every row written in one "
        "transaction ties to the microsecond, and SQL leaves tied rows in an unspecified order "
        "— which for LIMIT/OFFSET means a row can land on two pages or on none."
    )


def test_the_guard_can_see_a_paginated_query_at_all() -> None:
    """Vacuity check: if the chain detection breaks, the guard above passes on everything."""
    seen = 0
    for path in sorted(SRC_ROOT.rglob("infrastructure/*.py")):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign | ast.Return | ast.AnnAssign):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if ".limit(" in segment and ".offset(" in segment and ".order_by(" in segment:
                seen += 1
    assert seen > 0, (
        "found NO paginated (.limit + .offset + .order_by) query in src/gateway/**/infrastructure/ "
        "— the chain-detection heuristic this guard depends on has stopped matching, so the "
        "assertion above is now vacuous and would pass no matter what."
    )
