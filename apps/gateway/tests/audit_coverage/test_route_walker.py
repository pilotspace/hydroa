"""RED guard for audit-coverage-structural-guard (R9 release-hardening-p0) — the route walker.

Contract under test (audit-coverage-structural-guard TASK.md §RULES, DRAFT rev 2):
  M1 The guard walks every APIRoute of create_app() whose methods intersect
     {POST, PUT, PATCH, DELETE} and reaches a verdict on EACH — `audited` (a real
     record_audit(...) CALL in the handler's OWN IMMEDIATE package — e.g. gateway.evals.console,
     NEVER the whole gateway.evals tree) or `exempt` (a named, justified row).
     A route in neither bucket FAILS the guard, named by method + path + module.
  M3 Every exemption row carries (route, reason, task-citation) and the guard enforces that
     shape; the guard self-checks its census — non-empty, above a floor, and still finding
     known-audited and known-mutating routes, so a collection change cannot hollow it out.
  M7 Every exemption reason comes from a CLOSED vocabulary the guard enforces —
     read-only-POST · no-tenant-state · covered-by:<stream> · deferred:<task> — so a freeze
     review is mechanical and the deferred rows are a COUNTABLE backlog, not free-text silence.
  A7 An unclassifiable route (no methods, or an endpoint module outside `gateway.*`) is a
     VIOLATION, never a silent skip — fail-closed.

WHY THIS SUITE IS RED AT THE CURRENT TREE:
  Five whole /v1 resource-CRUD modules — evals, vector_stores, finetune, memory,
  conversations — mutate tenant data with ZERO audit trail (no record_audit CALL anywhere
  under any of the five). The census finds 139 mutating APIRoutes; 104 are audited, 19 sit on
  the justified exemption rows below, and the remaining 16 are the five modules'
  STATE-CHANGING routes (E1). Build retrofits those 16; the guard then goes green and STAYS
  green, because the next silent module fails it on arrival
  ([[masked-gate-never-reached-a-verdict]]: a guard that reaches no verdict reports green —
  hence the anti-vacuity self-check).

WHY THE EVIDENCE RULE IS THE *IMMEDIATE* PACKAGE (M1, rev 2):
  The first draft judged the whole top-level package (gateway.evals). Under that rule, the
  moment evals/api/router.py gained a record_audit call, the JWT-authed console twin
  `PUT /admin/evals/sets/{set_id}/baseline` (gateway.evals.console) would be marked audited
  FOR FREE and vanish from the census — a route that emits nothing, absolved by a sibling.
  Judging the handler's own immediate package keeps the four evals surfaces
  (gateway.evals.api · gateway.evals.console · gateway.evals.runs.api ·
  gateway.evals.verdict.api) as four INDEPENDENT verdicts, so the console twin must earn its
  own call. Measured: this re-partitions the census without moving the totals — the same
  104/19/16 split, now resting on 16 immediate packages instead of 13 top-level ones.

  The residual A4 blind spot is NARROWED but not closed, and that is deliberate and booked:
  gateway.proxy.api still vouches for 17 routes (including /v1/chat/completions) on the
  strength of 3 call sites in that package, and gateway.tenants.api for 34. A new silent
  router added INSIDE an already-audited immediate package is still invisible here. A
  per-handler guard is the named follow-up (A4), never a claim this guard's green supports.

DO NOT weaken these tests to pass — that is Build's job. In particular: do NOT add the five
modules' state-changing routes to EXEMPTIONS. They are the intended red. The ONE row this
list legitimately carries for those modules is the read-only-POST row for
`POST /v1/memories/search` (A14).

This module is deliberately DATABASE-FREE (it takes no `app` / `db_session` fixture), so it
costs a route walk and a source scan in CI, not a schema reset. Route-walking precedent:
tests/realtime_relay/test_carveout_invariant.py:25. Anti-vacuity + justified-escape-hatch
precedent: tests/repo_hygiene/test_provider_egress_injection.py:195. SANCTIONED-EDIT row
convention: tests/guardrails/test_guardrails_core.py:1354.
"""

from __future__ import annotations

import ast
from pathlib import Path

from fastapi.routing import APIRoute

from gateway.main import create_app

# apps/gateway/tests/audit_coverage/<this file> -> parents[2] == apps/gateway
GATEWAY_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_SRC = GATEWAY_ROOT / "src" / "gateway"

# A3: "mutating" is defined by HTTP method on an APIRoute. WebSocket routes are excluded —
# the realtime relay already audits itself (proxy/api/realtime_relay_ws.py:256), and the
# carve-out invariant guard owns that surface.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# M1: what PROVES a route audits — a real `record_audit(...)` CALL (an AST Call node, never a
# substring; see _calls_record_audit) somewhere in the handler's OWN IMMEDIATE package. The
# substring form was bypassable by a COMMENT: this tree already carries four prose mentions
# (usage/application/retention_sweep.py:54,412,418,656), so five comments would have absolved
# every violation. See the module docstring for the scope rationale and the residual.
AUDIT_CALL = "record_audit"

# M3 / E4 anti-vacuity floor. The census measured 139 mutating routes on 2026-08-19. The floor
# is set far below that so ordinary route churn never trips it, while a collection change that
# hollows the walker out (a renamed router registry, a create_app() that stops mounting /v1)
# cannot leave this guard green and inert.
CENSUS_FLOOR = 30

# M3 / E4 sentinels. If the walker stops SEEING these, its verdicts mean nothing.
#   - a known-AUDITED route: the keys module emits record_audit from its own router.
#   - a known-MUTATING route: the chat-completions POST is the busiest mutating surface.
#   - a known-audited PACKAGE: gateway.tenants.api, the broadest audited surface there is.
SENTINEL_AUDITED_ROUTE = "POST /admin/keys"
SENTINEL_MUTATING_ROUTE = "POST /v1/chat/completions"
SENTINEL_AUDITED_PACKAGE = "gateway.tenants.api"

# ---------------------------------------------------------------------------
# M7 — the CLOSED exemption vocabulary
# ---------------------------------------------------------------------------
# Free text passes a shape rule while saying nothing: "not needed" plus a citation would have
# satisfied M3 completely. A closed vocabulary makes the freeze review mechanical and turns
# the deferred rows into a countable backlog instead of prose.
#
#   read-only-POST        this POST changes no state (a search/query verb); an event would be
#                         evidence NOISE, not evidence.
#   no-tenant-state       nothing tenant-scoped is mutated (platform/internal config), so
#                         there is no tenant principal to attribute and nothing to attribute.
#   covered-by:<stream>   the activity IS recorded, in a different evidence stream that the
#                         row names (e.g. covered-by:usage_records).
#   deferred:<task>       genuinely silent and genuinely should be audited — deferred to the
#                         named follow-up. THIS is the countable backlog.
EXACT_REASONS = frozenset({"read-only-POST", "no-tenant-state"})
PREFIX_REASONS = ("covered-by:", "deferred:")

# A `deferred:` row is a promise to come back; the other three are permanent statements of
# fact. Only the promises can go stale (see the redundancy check).
DEFERRED_PREFIX = "deferred:"

# The single remaining follow-up bucket. `deferred:audit-coverage-admin-surfaces` is DEAD —
# Tin promoted those rows into this task's scope at freeze, so nothing may cite it any more.
# `audit-coverage-v1-crud` is a real task node created by the orchestrator.
FOLLOWUP_V1_CRUD = "deferred:audit-coverage-v1-crud"

# S4 / M8 / A25 — the /admin surfaces Tin promoted into scope at freeze. These must reach
# `audited` on their OWN package's evidence; they may hold NO exemption row at all. The
# assertion below enforces that, so a future edit cannot re-silence a promoted route by
# exempting it instead of emitting — the same anti-gaming shape the five modules get.
#
# ⚠ NINE, not ten. `POST /admin/auth/access-requests` was promoted with these but is
# UNAUTHENTICATED (see its exemption row below); M8's "JWT Identity as actor" is unsatisfiable
# there, so it is escalated rather than silently given an invented actor.
PROMOTED_ADMIN_ROUTES = frozenset(
    {
        "POST /admin/domain-claims",
        "DELETE /admin/domain-claims/{claim_id}",
        "POST /admin/domain-claims/{claim_id}/verify",
        "POST /admin/domain-claims/{claim_id}/member-verify",
        "POST /admin/domain-claims/{claim_id}/member-verify/resend",
        "POST /admin/domain-claims/{claim_id}/notify",
        "DELETE /admin/domain-claims/{claim_id}/notify",
        "PUT /admin/models/{model_id:path}",
        "POST /admin/catalog/sync",
    }
)

TASK_CITATION = (
    "audit-coverage-structural-guard TASK.md A2 — retrofit candidate, Tin review at freeze"
)


def reason_label(reason: str) -> str:
    """The machine-checkable label: everything before an optional ` — <human note>`."""
    return reason.split(" — ", 1)[0].strip()


def reason_is_in_vocabulary(reason: str) -> bool:
    """M7: the label must be an exact token or a non-empty prefixed token."""
    label = reason_label(reason)
    if label in EXACT_REASONS:
        return True
    return any(label.startswith(prefix) and len(label) > len(prefix) for prefix in PREFIX_REASONS)


# ---------------------------------------------------------------------------
# The exemption list (M3 · M7 · A2 · A14 · R:BARE_EXEMPTION)
# ---------------------------------------------------------------------------
# A2: the milestone exit says "every mutating gateway route", but THIS task retrofits only the
# five named silent modules. Every OTHER silent offender the census found at authoring time
# lands here, on a row that states WHY (from the closed vocabulary) and cites the task that put
# it there — reviewed line-by-line at freeze. A row Tin promotes becomes retrofit scope or a
# follow-up task; the walker does not change when a row is promoted, it just loses a row.
#
# SHAPE IS ENFORCED (E3, R:BARE_EXEMPTION): key is "METHOD path", value is
# (reason, task-citation), and the reason's LABEL must be in the closed vocabulary (M7, E6).
# A bare path buys no silence, and neither does an eloquent one — _shape_complaints() is
# self-tested against synthetic malformed AND off-vocabulary rows so enforcement cannot go
# vacuous.
#
# ⚠ FREEZE REVIEW — the deferred backlog is 17 rows in two buckets:
#   deferred:audit-coverage-admin-surfaces (10)  ← domain_capture 7, catalog 2, access_requests 1
#   deferred:audit-coverage-v1-crud        (7)   ← artifacts 2, files 2, responses_store 1,
#                                                  batches 1, video 1
# `domain_capture` is the highest-value promotion candidate on this list: /admin/domain-claims
# is the TENANT-BOUNDARY control surface — a claim verify decides which tenant an email domain
# joins — and it is currently silent. Promote those 7 into scope or into an immediate
# follow-up ahead of the /v1 CRUD rows.
EXEMPTIONS: dict[str, tuple[str, str]] = {
    # --- A14: the ONE row this list carries for a retrofit module ----------------------
    # POST /v1/memories/search is a pure READ that happens to use POST (memory/api/router.py:378
    # loads, ranks and returns; it writes nothing). A3 defines "mutating" by HTTP METHOD, so the
    # census sees it; A14 says it is NOT retrofitted, because a `memory.search` event would be
    # evidence noise rather than evidence. The row is checked BEFORE the audited verdict
    # precisely so it survives the retrofit: once memory/api/router.py emits, the immediate
    # package would otherwise absorb this route silently and the deliberate decision would
    # disappear from the record.
    "POST /v1/memories/search": (
        "read-only-POST — search over the caller's own memories; loads, ranks, returns, "
        "writes nothing (memory/api/router.py:378)",
        TASK_CITATION,
    ),
    # --- no-tenant-state ---------------------------------------------------------------
    "POST /internal/catalog/sync": (
        "no-tenant-state — internal scheduler-driven catalog refresh; blocked at the edge "
        "(Envoy denies /internal), no tenant principal and no tenant-scoped row to attribute",
        TASK_CITATION,
    ),
    # --- ESCALATED: promoted with the other nine, but structurally cannot take M8 --------
    # Tin promoted ten /admin rows at freeze. NINE are genuinely JWT-authed and are now
    # retrofit targets (PROMOTED_ADMIN_ROUTES above). This one is not:
    #
    #   access_requests_router = APIRouter(prefix="/admin/auth", tags=["access-requests-public"])
    #   async def create_access_request(body, request, use_case) -> ...   # no Identity, no authz
    #
    # It shares the /admin/auth prefix but is the PUBLIC signup-refusal intake — an
    # unauthenticated person asks for access. A24's premise ("the JWT Identity these routes
    # already authenticate") does not hold, and M8's "JWT Identity as actor" is unsatisfiable:
    # there is no user, no email beyond the untrusted body, and no tenant.
    #
    # Worse, auditing it fights its own security contract. The handler is deliberately
    # anti-enumeration (signup-refusal-router R3: no branch may read resolve_verified_tenant,
    # any user/tenant table, or any domain-claim state). Attributing an event to a tenant would
    # require resolving one from the submitted email — exactly the branch R3 forbids — and would
    # let an AUDIT_READ user of that tenant learn who tried to request access. That is the
    # enumeration oracle this route exists to close.
    #
    # So this row STAYS, labelled by the same reasoning A27 applies to /internal/catalog/sync,
    # and the finding is escalated for a Specify amendment rather than resolved by inventing an
    # actor. Do NOT retrofit this route on the strength of M8 as currently written.
    "POST /admin/auth/access-requests": (
        "no-tenant-state — PUBLIC unauthenticated signup-refusal intake (router tagged "
        "access-requests-public); no JWT Identity, no tenant principal, and resolving one "
        "would re-open the enumeration oracle the route closes. ESCALATED: M8/A24 cannot be "
        "satisfied here — see the comment above",
        TASK_CITATION,
    ),
    # --- deferred:audit-coverage-v1-crud (7) --------------------------------------------
    "POST /v1/artifacts": (
        f"{FOLLOWUP_V1_CRUD} — pre-existing silent /v1 resource CRUD (artifact create)",
        TASK_CITATION,
    ),
    "DELETE /v1/artifacts/{artifact_id}": (
        f"{FOLLOWUP_V1_CRUD} — pre-existing silent /v1 resource CRUD; a tenant-data DELETE, "
        "so the strongest candidate in this bucket",
        TASK_CITATION,
    ),
    "POST /v1/files": (
        f"{FOLLOWUP_V1_CRUD} — pre-existing silent /v1 resource CRUD (file upload)",
        TASK_CITATION,
    ),
    "DELETE /v1/files/{file_id}": (
        f"{FOLLOWUP_V1_CRUD} — pre-existing silent /v1 resource CRUD; a tenant-data DELETE",
        TASK_CITATION,
    ),
    "DELETE /v1/responses/{response_id}": (
        f"{FOLLOWUP_V1_CRUD} — pre-existing silent /v1 resource CRUD (stored-response delete); "
        "a tenant-data DELETE",
        TASK_CITATION,
    ),
    "POST /v1/batches": (
        f"{FOLLOWUP_V1_CRUD} — pre-existing silent /v1 resource CRUD (batch job create); "
        "per-item spend reaches usage_records, but the job creation itself is unrecorded",
        TASK_CITATION,
    ),
    "POST /v1/video/generations": (
        f"{FOLLOWUP_V1_CRUD} — pre-existing silent /v1 resource CRUD (video generation job); "
        "provider cost reaches usage_records, but the job creation itself is unrecorded",
        TASK_CITATION,
    ),
}


# ---------------------------------------------------------------------------
# Census
# ---------------------------------------------------------------------------


class RouteVerdict:
    """One mutating route and the bucket the walker put it in."""

    __slots__ = ("key", "method", "module", "package", "path", "verdict", "why")

    def __init__(
        self,
        *,
        method: str,
        path: str,
        module: str | None,
        package: str | None,
        verdict: str,
        why: str,
    ) -> None:
        self.key = f"{method} {path}"
        self.method = method
        self.path = path
        self.module = module
        self.package = package
        self.verdict = verdict  # "audited" | "exempt" | "violation" | "unclassifiable"
        self.why = why

    def __repr__(self) -> str:  # pragma: no cover — diagnostics only
        return f"<{self.key} [{self.verdict}] {self.module}>"


def _calls_record_audit(source: str) -> bool:
    """True when this module's AST contains a real `record_audit(...)` CALL.

    Deliberately NOT a substring scan. A raw `"record_audit(" in text` test also matches the
    string inside a comment or a docstring — and the tree already contains four such prose
    mentions (usage/application/retention_sweep.py:54,412,418,656 and ops/api/deps.py). That
    made the whole guard bypassable by writing a comment, which is exactly the shape of cheat
    a compliance-evidence guard must not accept: a package could be marked `audited` while
    emitting nothing. Parsing to an AST and looking for a Call node makes the evidence a real
    call site or nothing.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover — unparsable source
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name == AUDIT_CALL:
            return True
    return False


def _package_dir(package: str) -> Path | None:
    """Directory for a DOTTED gateway package, e.g. gateway.evals.console."""
    parts = package.split(".")
    if not parts or parts[0] != "gateway":
        return None
    directory = GATEWAY_SRC.joinpath(*parts[1:]) if len(parts) > 1 else GATEWAY_SRC
    return directory if directory.is_dir() else None


def _package_emits_audit(package: str) -> bool:
    """True when any .py in this IMMEDIATE package really calls record_audit().

    M1 (rev 2): the unit of evidence is the handler's own immediate package
    (gateway.evals.console), never the whole feature tree (gateway.evals) — otherwise a
    sibling's retrofit absolves a route that emits nothing. See the module docstring.
    """
    directory = _package_dir(package)
    if directory is None:
        return False
    for source in directory.rglob("*.py"):
        try:
            if _calls_record_audit(source.read_text()):
                return True
        except OSError:  # pragma: no cover — unreadable source
            continue
    return False


def _census() -> list[RouteVerdict]:
    """Walk create_app() and reach a verdict on EVERY mutating APIRoute.

    Fail-closed (A7): a route with no methods, an endpoint with no resolvable module, or a
    module outside `gateway.*` is `unclassifiable` — which counts as a violation. The walker
    never silently skips a route it cannot classify (R:VACUOUS_WALKER).
    """
    app = create_app()
    emits_cache: dict[str, bool] = {}
    verdicts: list[RouteVerdict] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = set(getattr(route, "methods", None) or ()) & MUTATING_METHODS
        path = getattr(route, "path", repr(route))

        if not methods:
            # Either a read-only route (no overlap — skipped legitimately) or an APIRoute
            # carrying no methods at all. Distinguish: only the latter is unclassifiable.
            if not (getattr(route, "methods", None) or ()):
                verdicts.append(
                    RouteVerdict(
                        method="<no-methods>",
                        path=path,
                        module=getattr(route.endpoint, "__module__", None),
                        package=None,
                        verdict="unclassifiable",
                        why="APIRoute declares no HTTP methods — cannot be judged mutating "
                        "or not, so it fails closed (A7)",
                    )
                )
            continue

        # M1: the handler's OWN IMMEDIATE package — gateway.evals.console.router yields
        # gateway.evals.console, so the console twin is judged apart from gateway.evals.api.
        module = getattr(route.endpoint, "__module__", None)
        package: str | None = None
        if isinstance(module, str) and module.startswith("gateway.") and "." in module:
            candidate = module.rpartition(".")[0]
            if _package_dir(candidate) is not None:
                package = candidate

        for method in sorted(methods):
            if package is None and f"{method} {path}" in EXEMPTIONS:
                # An unclassifiable route still has an EXIT: a justified exemption row. Without
                # this branch a route the walker cannot resolve to a package (a handler defined
                # in gateway/main.py, a third-party router) would be permanently unfixable
                # except by editing the walker — and a guard nobody can satisfy is a guard that
                # gets deleted.
                verdicts.append(
                    RouteVerdict(
                        method=method,
                        path=path,
                        module=module,
                        package=None,
                        verdict="exempt",
                        why=EXEMPTIONS[f"{method} {path}"][0],
                    )
                )
                continue
            if package is None:
                verdicts.append(
                    RouteVerdict(
                        method=method,
                        path=path,
                        module=module,
                        package=None,
                        verdict="unclassifiable",
                        why=f"endpoint module {module!r} does not resolve to a package under "
                        f"{GATEWAY_SRC} — the walker cannot look for record_audit, so it "
                        "fails closed (A7)",
                    )
                )
                continue

            if package not in emits_cache:
                emits_cache[package] = _package_emits_audit(package)

            key = f"{method} {path}"
            # EXEMPTION IS CHECKED FIRST, and that ordering is load-bearing (A14).
            # POST /v1/memories/search shares its immediate package with memory create/delete.
            # Under an audited-first rule, the moment Build retrofits that package the
            # read-only-POST row would be absorbed into a silent `audited` verdict and the
            # DELIBERATE decision not to audit a search would vanish from the record — while
            # A14's probe explicitly requires the walker to keep carrying that row. Checking
            # the row first keeps every deliberate exemption visible in the census for as long
            # as it is declared, whatever its package does later.
            if key in EXEMPTIONS:
                verdict, why = "exempt", EXEMPTIONS[key][0]
            elif emits_cache[package]:
                verdict, why = "audited", f"{package} calls {AUDIT_CALL}()"
            else:
                verdict, why = (
                    "violation",
                    f"{package} contains NO {AUDIT_CALL}() call",
                )
            verdicts.append(
                RouteVerdict(
                    method=method,
                    path=path,
                    module=module,
                    package=package,
                    verdict=verdict,
                    why=why,
                )
            )

    # A9: order is immaterial to correctness; sort for a stable failure message.
    verdicts.sort(key=lambda v: (str(v.package), v.path, v.method))
    return verdicts


def _shape_complaints(rows: dict[str, object]) -> list[str]:
    """Enforce the exemption ROW SHAPE and its CLOSED VOCABULARY (M3, M7, E3, E6).

    A row must be "METHOD path" -> (reason, task-citation), and the reason's LABEL — the text
    before an optional " — <human note>" — must come from the closed vocabulary (M7).

    The vocabulary is what makes the review mechanical. A shape rule alone is satisfied by
    eloquent nothing: "this route does not really need auditing" plus a citation would have
    passed the previous draft completely, and a reviewer would have had to re-derive the claim
    from scratch for all 19 rows. With labels, the freeze review reads a table: two permanent
    statements of fact (read-only-POST, no-tenant-state), one pointer to another evidence
    stream (covered-by:), and a countable backlog (deferred:).
    """
    complaints: list[str] = []
    for key, value in rows.items():
        if len(key.split(" ", 1)) != 2:
            complaints.append(f"{key!r}: key must be 'METHOD path' (BARE_EXEMPTION)")
            continue
        method, _, path = key.partition(" ")
        if method not in MUTATING_METHODS:
            complaints.append(f"{key!r}: {method!r} is not a mutating method")
        if not path.startswith("/"):
            complaints.append(f"{key!r}: path must start with '/'")
        if not isinstance(value, tuple) or len(value) != 2:
            complaints.append(
                f"{key!r}: value must be a (reason, task-citation) 2-tuple, got {value!r} "
                "— a bare path buys no silence (BARE_EXEMPTION)"
            )
            continue
        reason, citation = value
        if not isinstance(reason, str) or not reason.strip():
            complaints.append(
                f"{key!r}: reason is missing ({reason!r}) — state WHY this route may stay "
                "silent (BARE_EXEMPTION)"
            )
        elif not reason_is_in_vocabulary(reason):
            complaints.append(
                f"{key!r}: reason label {reason_label(reason)!r} is OUTSIDE the closed "
                f"vocabulary (M7, E6). Use one of: {sorted(EXACT_REASONS)} exactly, or a "
                f"non-empty {list(PREFIX_REASONS)} token. Free text reads like a "
                "justification while asserting nothing checkable."
            )
        if not isinstance(citation, str) or "TASK.md" not in citation:
            complaints.append(
                f"{key!r}: citation must name the task that granted the exemption, got "
                f"{citation!r} (BARE_EXEMPTION)"
            )
    return complaints


def _render(verdicts: list[RouteVerdict]) -> str:
    """Full census, grouped by verdict — printed on failure so the freeze review can read it."""
    lines: list[str] = []
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
    lines.append(
        "FULL CENSUS: "
        + ", ".join(f"{name}={counts.get(name, 0)}" for name in sorted(counts))
        + f" (total {len(verdicts)})"
    )
    for bucket in ("audited", "exempt"):
        packages: dict[str, int] = {}
        for verdict in verdicts:
            if verdict.verdict == bucket:
                packages[str(verdict.package)] = packages.get(str(verdict.package), 0) + 1
        lines.append(
            f"  {bucket}: " + ", ".join(f"{pkg}({n})" for pkg, n in sorted(packages.items()))
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def test_every_mutating_route_audited_or_exempt() -> None:
    """covers: M1, M3, M7, A2, A3, A4, A7, A9, A10, A11, A12, A14, E1, E3, E4, E6, R:VACUOUS_WALKER, R:BARE_EXEMPTION

    The walker: full census, a per-route verdict, justified-exemption shape AND closed
    vocabulary enforced, anti-vacuity (census floor + known-audited and known-mutating
    sentinels present).

    RED at the current tree, naming the five silent modules' 16 state-changing routes. It goes
    green when Build retrofits evals / vector_stores / finetune / memory / conversations —
    including the JWT-authed console twin, which under M1's immediate-package rule must earn
    its OWN record_audit call and cannot ride gateway.evals.api's. NOT by adding those routes
    to EXEMPTIONS.
    """
    # --- E4 / M3: the guard must never be able to pass vacuously ----------------------
    verdicts = _census()
    assert verdicts, (
        "VACUOUS_WALKER: create_app() yielded ZERO mutating APIRoutes. Either the app stopped "
        "mounting its routers or APIRoute stopped being the route class — either way this "
        "guard is now covering nothing. Fix the walker; do not delete the assertion."
    )
    assert len(verdicts) >= CENSUS_FLOOR, (
        f"VACUOUS_WALKER: the census collapsed to {len(verdicts)} mutating routes, below the "
        f"floor of {CENSUS_FLOOR}. A guard whose population silently shrank is a guard that "
        "reports green without reaching a verdict.\n" + _render(verdicts)
    )

    by_key = {verdict.key: verdict for verdict in verdicts}
    assert SENTINEL_MUTATING_ROUTE in by_key, (
        f"VACUOUS_WALKER: the known-mutating sentinel {SENTINEL_MUTATING_ROUTE!r} is no longer "
        "in the census. The walker has lost sight of the busiest mutating surface in the app, "
        "so its verdicts prove nothing about coverage."
    )
    audited_sentinel = by_key.get(SENTINEL_AUDITED_ROUTE)
    assert audited_sentinel is not None and audited_sentinel.verdict == "audited", (
        f"VACUOUS_WALKER: the known-AUDITED sentinel {SENTINEL_AUDITED_ROUTE!r} is missing or "
        f"no longer classifies as audited (got {audited_sentinel!r}). The immediate-package "
        "evidence rule has stopped working — every 'audited' verdict below is now suspect."
    )
    audited_packages = {v.package for v in verdicts if v.verdict == "audited"}
    assert SENTINEL_AUDITED_PACKAGE in audited_packages, (
        f"VACUOUS_WALKER: package {SENTINEL_AUDITED_PACKAGE!r} no longer classifies as "
        "audited — it is the broadest audited surface in the app, so losing it means the "
        "evidence rule regressed and every audited verdict is now suspect."
    )

    # M1 (rev 2) is only real if the four evals surfaces are judged SEPARATELY. If a refactor
    # collapses them back to one package, the console twin starts riding its siblings' call
    # and silently leaves the census — the exact defect the rev-2 tightening exists to stop.
    evals_packages = {
        v.package for v in verdicts if v.package and v.package.startswith("gateway.evals")
    }
    assert len(evals_packages) >= 4, (
        "M1 REGRESSION: the evals surfaces collapsed into "
        f"{sorted(evals_packages)} — fewer than the four independent immediate packages "
        "(gateway.evals.api · gateway.evals.console · gateway.evals.runs.api · "
        "gateway.evals.verdict.api). Under a coarser rule the JWT console twin "
        "PUT /admin/evals/sets/{set_id}/baseline is marked audited for free by a sibling's "
        "record_audit call and vanishes from this census while emitting nothing."
    )

    # --- M8 / A25 anti-gaming: a promoted route may NOT buy silence with a row --------
    # Tin promoted these into scope at freeze precisely because they were silent. The only
    # honest exit for them is to EMIT. Without this assert, the cheapest way to make the guard
    # green again would be to move them back onto the exemption list — turning a freeze
    # decision into a one-line edit. Same shape as the five modules' anti-gaming rule.
    re_silenced = sorted(PROMOTED_ADMIN_ROUTES & set(EXEMPTIONS))
    assert not re_silenced, (
        f"{len(re_silenced)} PROMOTED /admin route(s) were put back on the exemption list. "
        "Tin promoted these into retrofit scope at freeze (M8/A25) — they must EMIT an audit "
        "event, not buy silence with a row:\n  " + "\n  ".join(re_silenced)
    )
    missing_from_census = sorted(PROMOTED_ADMIN_ROUTES - set(by_key))
    assert not missing_from_census, (
        f"{len(missing_from_census)} promoted /admin route(s) are no longer in the census — "
        "the walker has lost sight of routes it is supposed to be policing:\n  "
        + "\n  ".join(missing_from_census)
    )

    # --- M3 hygiene: no exemption row may outlive the route it excuses ----------------
    # Without this, a row whose route was deleted or retrofitted rots in the list forever,
    # and the next reviewer reads a stale excuse as a live decision.
    stale = sorted(set(EXEMPTIONS) - set(by_key))
    assert not stale, (
        f"{len(stale)} exemption row(s) name a route that is no longer in the census — delete "
        "them; a stale excuse reads like a live decision at the next review:\n  "
        + "\n  ".join(stale)
    )
    # A `deferred:` row is a PROMISE to come back; once its package emits, the promise is
    # kept and the row must go, or the backlog over-counts forever. The other three labels are
    # permanent statements of fact — read-only-POST in particular MUST survive its package's
    # retrofit (A14: POST /v1/memories/search shares a package with memory create/delete), so
    # narrowing this check to deferred rows is what lets exemption-first ordering stay honest.
    kept_promises = sorted(
        key
        for key, (reason, _) in EXEMPTIONS.items()
        if reason_label(reason).startswith(DEFERRED_PREFIX)
        and key in by_key
        and _package_emits_audit(str(by_key[key].package))
    )
    assert not kept_promises, (
        f"{len(kept_promises)} deferred exemption row(s) are now redundant — their package "
        "emits audit events, so the deferral was honoured. Delete the rows so the backlog "
        "count stays truthful:\n  " + "\n  ".join(kept_promises)
    )

    # --- E3 / M3 / R:BARE_EXEMPTION: the declared rows must carry reason + citation ----
    complaints = _shape_complaints(dict(EXEMPTIONS))
    assert not complaints, (
        f"{len(complaints)} malformed exemption row(s) — every row must be "
        '"METHOD path" -> (reason, task-citation):\n  ' + "\n  ".join(complaints)
    )

    # The shape rule is itself checked against synthetic bad rows, so E3 can never go
    # vacuous by _shape_complaints() quietly becoming a no-op.
    bad_rows: dict[str, object] = {
        "POST /bare": ("", TASK_CITATION),  # no reason
        "POST /uncited": ("read-only-POST", ""),  # no citation
        "POST /naked": "just a string",  # not a 2-tuple
        "/no-method": ("read-only-POST", TASK_CITATION),
        # E6 — the rows that make the CLOSED VOCABULARY real. Each has a genuine reason AND a
        # genuine citation, and each must still fail: an eloquent excuse is still free text.
        "POST /eloquent": (
            "this endpoint does not really mutate anything important, and the team agreed "
            "at review that auditing it would add noise without adding evidence",
            TASK_CITATION,
        ),
        "POST /near-miss": ("read only POST", TASK_CITATION),  # spaces, not the exact token
        "POST /empty-defer": ("deferred:", TASK_CITATION),  # prefix with no task named
        "POST /empty-stream": ("covered-by:", TASK_CITATION),  # prefix with no stream named
    }
    for key in bad_rows:
        assert _shape_complaints({key: bad_rows[key]}), (
            f"the exemption shape rule accepted a malformed row {key!r} — E3 would be "
            "vacuous, and a bare path would buy silence (R:BARE_EXEMPTION)"
        )

    # --- A7: nothing may be silently skipped ------------------------------------------
    unclassifiable = [v for v in verdicts if v.verdict == "unclassifiable"]
    assert not unclassifiable, (
        f"{len(unclassifiable)} route(s) the walker could not classify — fail-closed (A7), "
        "an unknown route is a violation, never a skip:\n  "
        + "\n  ".join(f"{v.key} <- {v.module} :: {v.why}" for v in unclassifiable)
        + "\n\nTWO WAYS OUT: move the handler under a gateway/<package>/ subtree so the "
        "module-subtree rule can judge it, or add a justified EXEMPTIONS row for it in this "
        "file (unclassifiable routes consult the exemption list too — see _census)."
    )

    # --- M1 / E1: the verdict -----------------------------------------------------------
    violations = [v for v in verdicts if v.verdict == "violation"]
    assert not violations, (
        f"{len(violations)} mutating route(s) emit NO audit event and hold NO justified "
        "exemption. Every mutating gateway route must reach one of those two buckets.\n\n"
        + "\n".join(f"  {v.key}  <-  {v.module}   ({v.why})" for v in violations)
        + "\n\nTWO WAYS OUT — pick the right one:\n"
        "  1. EMIT: after the mutating commit succeeds, await record_audit(\n"
        "     request.app.state.sessionmaker, AuditEvent(action='<module>.<verb>',\n"
        "     target_type='<bare noun>', target_id=<resource id>, tenant_id=authz.tenant_id,\n"
        "     actor_key_id=authz.key_id, actor_user_id=None, ...)). This is the correct exit\n"
        "     for the five modules named in audit-coverage-structural-guard.\n"
        "  2. EXEMPT: add a row to EXEMPTIONS in this file keyed 'METHOD path' with a\n"
        "     (reason, task-citation) pair whose reason LABEL is in the closed vocabulary\n"
        "     (read-only-POST | no-tenant-state | covered-by:<stream> | deferred:<task>).\n"
        "     A bare path is refused, and so is free text (R:BARE_EXEMPTION, M7).\n\n"
        + _render(verdicts)
    )
