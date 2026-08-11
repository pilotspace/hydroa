"""Standing guard: a suite that reads a SINGLETON table must clear it (CR v3, class 4).

A singleton table — one whose primary key is pinned by `CheckConstraint("id IS TRUE")` —
holds exactly ONE row for the entire database. It is therefore cross-test global state for
every suite that reads it, in a way a tenant-scoped table never is.

Most suites never notice. The root `app` fixture rebuilds the schema and DELETEs every
table per test, so the row cannot survive. The hazard is a suite that builds its OWN app in
its conftest (`create_app(...)` directly, usually to inject custom Settings) and thereby
opts out of that cleanup while still pointing at the shared per-worker database.

That is not hypothetical. `tests/routing_admin` lost five tests to
`model_groups: {'gpt': ['a']}` — a value it never sets, left behind by
`tests/routing_config_write`, which ends by persisting exactly that. It was recorded as an
unexplained app.state/Settings leak (todo #99) after four probes failed to reproduce it,
and the reason it was mis-diagnosed is instructive: the suite's conftest says "no DB schema
needed", which was read as "no DB involvement". It means the opposite — the suite never
REBUILDS the schema, which is precisely why a leftover row is visible to it.

Under `--dist loadscope` whether the polluter lands on the same worker first is a
scheduling accident, so the failure rotates between runs.

Covers CR v3 class 4.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = GATEWAY_ROOT / "tests"
SRC_ROOT = GATEWAY_ROOT / "src"

# The ORM marker for "this table has exactly one row, forever".
SINGLETON_CHECK = re.compile(r'CheckConstraint\(\s*["\']id IS TRUE["\']')
TABLENAME = re.compile(r'__tablename__\s*=\s*["\']([^"\']+)["\']')

# A suite that builds its own app opts out of the root fixture's per-test DELETE.
OWN_APP = "create_app("

ROUTER_PREFIX = re.compile(r'APIRouter\(\s*prefix\s*=\s*["\']([^"\']+)["\']')


def _identifiers_for(table: str) -> tuple[str, ...]:
    """The spellings a src module uses to reach `routing_config`: the table name itself and
    its CamelCase form (`RoutingConfig`, matching `RoutingConfigRow`/`Repository`)."""
    camel = "".join(part.capitalize() for part in table.split("_"))
    return (table, camel)


def singleton_reading_routes() -> dict[str, set[str]]:
    """Map each singleton table to the route prefixes that read it.

    Needed because the suite this guard exists for NEVER NAMES THE TABLE. `routing_admin`
    reads the singleton through `GET /admin/routing`, so a table-name search over the suite
    finds nothing — the first version of this guard was GREEN against the exact tree whose
    failure motivated it, which is the worst possible outcome for a guard. Matching on the
    ROUTE closes that hole without falling back on "every suite that builds its own app must
    clear every singleton table": that rule flags 29 suites which never touch routing config,
    and a guard that demands 29 unnecessary DELETEs trains people to paste one without
    reading, which is how the real hazard got missed in the first place.

    Known limit, stated rather than papered over: the link is a ONE-HOP substring match. It
    holds here because a router that reads the table imports it by a name carrying the
    table's own spelling (`from ...routing_config_repository import RoutingConfigRepository`),
    so the import line itself is the evidence. A router reaching a singleton through a
    differently-named indirection would not be linked, and the `no singleton tables found`
    branch below is the only self-check against the whole guard silently covering nothing.
    """
    routes: dict[str, set[str]] = {}
    # The app-assembly module is excluded because co-location proves nothing there: main.py
    # imports every table in the schema AND declares routers for unrelated concerns, so it
    # linked the singleton to `/internal` and dragged in three suites that merely mention
    # that prefix. A router module, by contrast, declares exactly the routes it serves.
    assembly_root = SRC_ROOT / "gateway" / "main.py"
    sources = [(p, p.read_text()) for p in SRC_ROOT.rglob("*.py") if p != assembly_root]
    for table in singleton_tables():
        names = _identifiers_for(table)
        prefixes: set[str] = set()
        for _path, source in sources:
            if not any(name in source for name in names):
                continue
            prefixes.update(ROUTER_PREFIX.findall(source))
        routes[table] = prefixes
    return routes


def singleton_tables() -> set[str]:
    """Table names whose ORM class pins a singleton primary key.

    Derived from the source rather than hard-coded: a NEW singleton table is exactly the
    case this guard needs to cover without anyone remembering to update a list.
    """
    found: set[str] = set()
    for path in SRC_ROOT.rglob("*orm*.py"):
        source = path.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover — ruff's problem, not ours
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            body = ast.get_source_segment(source, node) or ""
            if not SINGLETON_CHECK.search(body):
                continue
            name = TABLENAME.search(body)
            if name is not None:
                found.add(name.group(1))
    return found


SQL_VERB = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|TRUNCATE|FROM|INTO)\b", re.IGNORECASE)


def _names_the_table(source: str, table: str) -> bool:
    """Does this module READ the table, as opposed to merely mentioning its name?

    The distinction is not pedantry. `tests/guardrails` lists `'routing_config'` inside a
    comma-separated table MANIFEST (the cross-manifest drift check) and names it in a
    comment; it never touches the row, and demanding a DELETE there would be pure ceremony.
    Meanwhile the real read in `tests/signup_routing_authz` lives INSIDE a string literal —
    `text("SELECT config FROM routing_config WHERE id IS TRUE")` — so blanket-skipping string
    literals, which is how the sibling guard avoids self-flagging, would discard the true
    positive along with the false one.

    So: comments never count; a string counts only when it is SQL; anything else (an
    identifier such as a `_routing_config_row` helper) counts.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):  # pragma: no cover — ruff's problem
        return table in source
    for tok in tokens:
        if table not in tok.string:
            continue
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING:
            if SQL_VERB.search(tok.string):
                return True
            continue
        return True
    return False


def _suite_dirs() -> list[Path]:
    return sorted({p.parent for p in TESTS_ROOT.rglob("conftest.py") if p.parent != TESTS_ROOT})


def _violations() -> list[str]:
    tables = singleton_tables()
    if not tables:  # pragma: no cover — would mean the ORM marker changed shape
        return [
            "no singleton tables found in src/ — the CheckConstraint('id IS TRUE') marker "
            "this guard keys off has changed, so it is now silently covering nothing"
        ]

    routes = singleton_reading_routes()

    found: list[str] = []
    for suite in _suite_dirs():
        conftest = suite / "conftest.py"
        conftest_src = conftest.read_text()
        if OWN_APP not in conftest_src:
            continue  # uses the root `app` fixture -> the per-test DELETE already covers it

        # Does anything in this suite clear the singleton table?
        suite_sources = [p.read_text() for p in suite.glob("*.py")]
        for table in sorted(tables):
            # Naming the table is the obvious way to read it; calling a route that reads it
            # is the way that actually bit us. Either one makes this suite a reader.
            by_name = any(_names_the_table(src, table) for src in suite_sources)
            by_route = any(
                prefix in src for prefix in routes.get(table, set()) for src in suite_sources
            )
            if not (by_name or by_route):
                continue
            clears = any(
                re.search(rf"DELETE\s+FROM\s+{re.escape(table)}\b", src, re.IGNORECASE)
                or re.search(rf"TRUNCATE\s+(TABLE\s+)?{re.escape(table)}\b", src, re.IGNORECASE)
                for src in suite_sources
            )
            if not clears:
                rel = suite.relative_to(GATEWAY_ROOT)
                how = "names it" if by_name else "calls a route that reads it"
                found.append(
                    f"{rel} builds its own app (create_app in conftest, so the root `app` "
                    f"fixture's per-test DELETE never runs) and reads the SINGLETON table "
                    f"{table!r} ({how}) without clearing it — a row left by any suite "
                    f"scheduled onto the same xdist worker first is visible here"
                )
    return sorted(found)


def test_the_route_link_this_guard_depends_on_still_resolves() -> None:
    """The by-route arm must not go dead silently.

    `_violations` finds a reader two ways: the suite NAMES the table, or the suite calls a
    ROUTE that reads it. The second arm is the one that matters — the suite whose failure
    motivated this guard never names the table, so the first version of the guard was GREEN
    against the exact tree it was written for.

    That arm rests on a one-hop substring link from table name to `APIRouter(prefix=...)`, and
    the docstring on `singleton_reading_routes` names its own limit: a router reaching the
    singleton through a differently-named indirection would not be linked. Nothing checked
    that the link still resolves — so a rename could quietly return the guard to its original
    blind state, passing all the while. This is that check.
    """
    routes = singleton_reading_routes()
    assert routes, "no singleton tables discovered — see the marker self-check in _violations"
    unlinked = sorted(table for table, prefixes in routes.items() if not prefixes)
    assert not unlinked, (
        f"these singleton tables resolve to NO route prefix: {unlinked}. The by-route arm of "
        "this guard is therefore inert for them, and a suite that reads the table only through "
        "an HTTP call — the exact case this guard exists for — would no longer be flagged. "
        "Either restore the naming link the match relies on (a router that reads the table "
        "imports something carrying the table's own spelling) or replace the heuristic; do not "
        "delete this assertion, which would leave the guard green and blind."
    )


def test_suite_reading_a_singleton_row_clears_it() -> None:
    """CR v3 class 4 — a singleton row is global state; a suite that reads it must own it.

    Two ways to satisfy this: use the root `app` fixture (which clears every table per
    test), or clear the table in the suite's own arrange. `routing_admin` needs the second
    because it injects custom Settings.
    """
    violations = _violations()
    assert not violations, (
        f"{len(violations)} suite(s) read a singleton table they do not clear:\n  "
        + "\n  ".join(violations)
    )
