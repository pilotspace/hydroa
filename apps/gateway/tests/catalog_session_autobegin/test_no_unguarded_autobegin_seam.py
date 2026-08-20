"""S3 of catalog-sync-session-autobegin — the READ-ONLY GUARD CENSUS.

WHAT THIS REPLACED, and why. The first version of this file swept for "a request-path
repository that owns `async with session.begin()` behind a revocation-guarded dependency"
and told the reader to "close the guards' read-only transaction in the dependency that
opened it". Two refute passes killed that design: a per-dependency close does not compose
(a nested dependency re-opens one), and keeping even two such closes as defence in depth
MASKS the runtime sweep in test_seam_session_state.py -- only 1 of 5 seams went red against
a deliberately broken primitive instead of 5 of 5. A guard that mandates a refuted remedy is
worse than no guard: it pushes the next engineer to re-introduce the defect. It also
resolved reachability by IMPORT ADJACENCY and was structurally blind to 3 of the 5 seams.

So this file now guards the thing that actually kept going wrong. The contract's A3 was
WRONG THREE TIMES -- "catalog only", then "catalog + keys", then "two guards" -- every time
because the class of read-only guards was enumerated BY HAND. The runtime sweep proves the
seams that exist today are clean; this census makes the class closed by CONSTRUCTION, so a
FOURTH guard of the same shape cannot be added without the restore.

DO NOT weaken this test to pass. If you are adding a read-only guard, add the restore.
"""

from __future__ import annotations

import ast
import pathlib

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "gateway"

# The three guards known today. This set is NOT the population the census checks -- it is
# the anti-vacuity floor: the census discovers its own population, and if that discovery
# ever stops seeing these three, its clean verdict is worthless and the test says so.
KNOWN_GUARDS = {
    "DbSessionRevocationGuard",
    "DbImpersonationSessionGuard",
    "DbUserLivenessGuard",
}

# Exemptions: a guard-shaped class that legitimately OWNS its transaction (it writes), so
# the restore rule does not apply. Each needs a reason, not just a name. Adding a name here
# is a deliberate act -- an empty dict means nothing has needed one yet.
NOT_READ_ONLY: dict[str, str] = {}


def _guard_names_by_construction(trees: dict[pathlib.Path, ast.Module]) -> set[str]:
    """The population, discovered rather than enumerated.

    A guard of this class is CONSTRUCTED with both `session=` (one it does not own) and
    `timeout_seconds=` (it is a bounded auth check, not a repository). That pair is the
    house shape of every auth guard here and is not used by any read repository -- which
    matters, because an earlier heuristic of "reads on an injected session" swept in 15
    ordinary read repositories and use cases. Those run INSIDE the handler body, where an
    open transaction is normal and harmless; only a guard that runs in the AUTH phase,
    before the handler, can strand one across a repository's own `session.begin()`.
    """
    names: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Both call styles, so a guard cannot hide behind an import convention:
            # `DbFooGuard(...)` (what this codebase does today) and `infra.DbFooGuard(...)`.
            if isinstance(node.func, ast.Name):
                called = node.func.id
            elif isinstance(node.func, ast.Attribute):
                called = node.func.attr
            else:
                continue
            kwargs = {k.arg for k in node.keywords}
            if {"session", "timeout_seconds"} <= kwargs:
                names.add(called)
    return names


def _has_conditional_restore(cls: ast.ClassDef) -> bool:
    """The M3 shape: sample `in_transaction()` BEFORE the read, and roll back only if this
    guard opened it. Both halves are required — an unconditional rollback is exactly the
    per-dependency mistake, and a sample with no rollback restores nothing.

    The SUCCESS-PATH qualifier is load-bearing, and a fourth refute pass is why it is here.
    An earlier version of this predicate asked only "does `rollback` appear anywhere in the
    class". Every guard also carries a best-effort rollback inside its `except` handler
    (M5), which satisfied that question on its own — so deleting the success-path restore
    outright left this census GREEN while the motivating InvalidRequestError 500 was live.
    A guard proven green on the defect it exists to catch is worse than no guard.

    So the rollback must be reachable when NOTHING was raised: at least one that is not
    nested inside an `except` handler.
    """
    samples = any(
        isinstance(n, ast.Attribute) and n.attr == "in_transaction" for n in ast.walk(cls)
    )

    handled: set[int] = set()
    for node in ast.walk(cls):
        if isinstance(node, ast.ExceptHandler):
            handled.update(id(child) for child in ast.walk(node))

    restores_on_success = any(
        isinstance(n, ast.Attribute) and n.attr == "rollback" and id(n) not in handled
        for n in ast.walk(cls)
    )
    return samples and restores_on_success


def _census() -> tuple[list[str], list[str]]:
    """-> (offenders, found_guard_names). Unparseable source is a FINDING, never a skip."""
    offenders: list[str] = []
    found: list[str] = []

    trees: dict[pathlib.Path, ast.Module] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        try:
            trees[path] = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:  # a mid-edit tree must fail LOUDLY, not silently pass
            offenders.append(f"{path.relative_to(SRC_ROOT)} — unparseable: {exc}")

    population = _guard_names_by_construction(trees)
    for path, tree in trees.items():
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if cls.name not in population or cls.name in NOT_READ_ONLY:
                continue
            found.append(cls.name)
            if not _has_conditional_restore(cls):
                offenders.append(
                    f"{path.relative_to(SRC_ROOT)}::{cls.name} — reads on a session it does "
                    f"not own and never restores the state it found"
                )
    return offenders, found


def test_every_read_only_session_guard_restores_what_it_opened() -> None:
    """covers: M3, M4, A3, A10, A11, A12, A13, S3, R:SILENT_CLASS — every read-only guard
    that reads on a session it does not own carries the conditional restore.

    RED against the pre-fix tree: all three known guards report as offenders.
    """
    offenders, found = _census()

    assert not offenders, (
        "R:SILENT_CLASS — a read-only guard reads on a SHARED session and leaves the "
        "transaction its SELECT autobegan open. Any repository that later calls "
        "`async with session.begin()` on that session raises InvalidRequestError. Add the "
        "M3 restore (sample `in_transaction()` before the read; roll back ONLY if this "
        "guard opened it) to the guard itself — NEVER at the dependency, which does not "
        "compose and masks the runtime sweep (M4):\n  - " + "\n  - ".join(offenders)
    )

    # Anti-vacuity: a census that finds nothing asserts nothing. If the detection
    # heuristics stop matching, this fails loudly instead of reporting a clean sweep.
    missing = KNOWN_GUARDS - set(found)
    assert not missing, (
        f"the census stopped recognising known read-only guards {sorted(missing)} — its "
        f"detection is broken, so its clean verdict is worthless. Found: {sorted(set(found))}"
    )
