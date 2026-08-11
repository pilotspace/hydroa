"""CI-workflow parity gates.

`.github/workflows/ci.yml` is a manifest like any other, and it drifts the same way
the table manifests drift (see `test_migrations.EXPECTED_TABLES`). Two drifts had
already accumulated unnoticed while CI was blocked at the account level and no run
ever reached step 1:

* the Postgres service image stayed `postgres:16-alpine` after #89 moved every other
  Postgres reference in the repo to `pgvector/pgvector:pg16` — so CI could not run
  `CREATE EXTENSION vector` and every vector-store suite would have failed;
* `allowlist-node` is a prerequisite of the `Makefile` `ci:` target and was never
  invoked by the workflow, leaving the node dependency allow-list unenforced.

These are pure text checks over repo files: no network, no docker, no GitHub API.
They also stand as the standing guard against "make CI green by deleting a step".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
KIND_E2E_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "kind-e2e.yml"
COMPOSE_DEV = REPO_ROOT / "infra" / "docker-compose.dev.yml"
CHART_VALUES = REPO_ROOT / "charts" / "ai-proxy" / "values.yaml"
MAKEFILE = REPO_ROOT / "Makefile"

# The exact image the project deploys. Bumping Postgres/pgvector means bumping
# this line AND all four manifests together — which is the whole point.
PGVECTOR_IMAGE = "pgvector/pgvector:pg16"

# The gateway job's wall-clock floor, set from a MEASUREMENT.
#
# The INVARIANT has never changed: the cap must outlast the unit of work the job
# actually performs. A cap below that is not a safety bound — it is a guaranteed
# `cancelled`, a check that never reaches a verdict, and a gate that proves nothing.
#
# What changed (PR #104) is the UNIT. The job used to run the whole suite; it now runs
# ONE SHARD of four. So this floor was re-derived, NOT relaxed — 120 was 1.5x the
# 1h22m15s whole-suite runtime, and 15 is 1.5x the longest measured shard. Holding 120
# against a 10-minute shard would have been the opposite of safety: a HUNG shard would
# have burned two hours before reporting, which is why it was todo #112.
#
# Measured shard durations, run 31468024262 (N=4, all green):
#   gateway (1) 9m37s   gateway (2) 9m23s   gateway (3) 9m34s   gateway (4) 9m56s
# Longest 9m56s -> floor 15 (x1.5). ci.yml itself sets 20 (x2), leaving room to tune
# down to this floor on evidence without editing this constant.
#
# Whole-suite history, all lower bounds because none of these caps ever completed:
#   30 -> ~30m       serial, every run on main from 2026-07-23
#   75 -> 74m17s     serial, run 31197251730
#   60 -> 59m27s     -n 4,   run 31241464171
#  120 -> held; the suite ran 1h06m-1h22m unsharded (run 31243949907)
#
# Re-derive this constant from an OBSERVED run, never from an extrapolation: the 75
# came from scaling a 12-core dev-host number to a 4-core runner and was wrong, and
# the 60 that replaced it was wrong too. Each mistake cost a cancelled run.
#
# If the shard count changes, this changes with it — fewer shards means a longer shard.
MIN_GATEWAY_TIMEOUT_MINUTES = 15

# `make ci` gates that CI satisfies in a DIFFERENT but equivalent form, so the literal
# `make <gate>` string is legitimately absent from the workflow.
#
# Only `test-ci` is exempt, and the exemption is NOT a hole: the substitution it permits
# (a sharded `test-ci-shard` matrix plus a combined-coverage job) is asserted in full by
# `test_the_sharded_test_gate_is_enforced_in_full`. Adding a name here without a companion
# assertion would be how a gate quietly stops being enforced — do not.
_SHARDED_GATES = frozenset({"test-ci"})


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text()))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    """The workflow's `on:` block, keyed the way the YAML loader actually returns it.

    `on` is a YAML 1.1 boolean, so `yaml.safe_load` parses the key as `True` —
    NOT the string `"on"`. A guard written as `workflow["on"]` raises `KeyError`
    and is one lazy "fix" away from being vacuously green, so read the boolean
    key first and accept the string only as a forward-compatible fallback (a
    future YAML-1.2 loader would yield `"on"` and must not silently blind this).
    """
    for key in (True, "on"):
        if key in workflow:
            return cast(dict[str, Any], workflow[key])
    raise AssertionError(f"workflow has no `on:` trigger block; keys={list(workflow)}")


def _ci_postgres_image() -> str:
    workflow = _load(CI_WORKFLOW)
    services = workflow["jobs"]["gateway"]["services"]
    return cast(str, services["postgres"]["image"])


def _compose_postgres_image() -> str:
    compose = _load(COMPOSE_DEV)
    for service in compose["services"].values():
        image = str(service.get("image", ""))
        if "postgres" in image or "pgvector" in image:
            return image
    raise AssertionError(f"no postgres service found in {COMPOSE_DEV}")


def _chart_postgres_image() -> str:
    values = _load(CHART_VALUES)
    return cast(str, values["datastores"]["postgres"]["image"])


def _gateway_unconditional_run_steps() -> list[str]:
    """The `run:` bodies of the gateway job's UNCONDITIONAL steps.

    Three narrowings, each closing a way a gate could look enforced but not be:

    * only the `gateway` job — a gate planted in `dashboard` (which has a
      different working-directory and toolchain) does not enforce anything here;
    * only steps with no `if:` — `if: false`, or any condition, means the gate
      may never execute. A conditional gate is not a gate;
    * `run:` bodies only — a `uses:` step cannot invoke a Makefile target.
    """
    workflow = _load(CI_WORKFLOW)
    gateway = workflow["jobs"]["gateway"]
    return [
        str(step["run"])
        for step in gateway.get("steps", [])
        if step.get("run") and "if" not in step
    ]


def _make_ci_prerequisites() -> list[str]:
    match = re.search(r"^ci:(.*)$", MAKEFILE.read_text(), flags=re.MULTILINE)
    assert match is not None, "no `ci:` target in the Makefile"
    return match.group(1).split()


def test_ci_postgres_image_has_pgvector() -> None:
    """CI must run a Postgres that can serve `CREATE EXTENSION vector`.

    `tests/conftest.py` creates the extension for every gateway test database and
    the ORM metadata carries a `Vector(1536)` column, so a stock/alpine Postgres
    fails the entire vector-store and file-search surface.

    Asserts the EXACT image the project deploys, not a substring: a bare
    `"pgvector" in image` check passes on `pgvector-but-not-really:latest` or a
    typo'd tag, which is a green that proves nothing.
    """
    image = _ci_postgres_image()
    assert image == PGVECTOR_IMAGE, (
        f"ci.yml postgres service is {image!r}, expected {PGVECTOR_IMAGE!r}. "
        "Only the pinned pgvector image is known to serve `CREATE EXTENSION "
        "vector` — every vector-store suite would fail in CI otherwise."
    )


def test_ci_postgres_image_matches_compose_and_chart() -> None:
    """The CI Postgres image must not drift from the deployed one.

    #89 bumped compose and the chart and left ci.yml behind; any future Postgres or
    pgvector bump would re-introduce exactly that drift.
    """
    images = {
        "ci.yml": _ci_postgres_image(),
        "docker-compose.dev.yml": _compose_postgres_image(),
        "charts/ai-proxy/values.yaml": _chart_postgres_image(),
    }
    assert len(set(images.values())) == 1, (
        f"Postgres image drift across manifests: {images} — CI must test against the "
        "same Postgres the project deploys"
    )


def test_ci_enforces_every_make_ci_gate() -> None:
    """The workflow's gate set must be a superset of `make ci`.

    Red when a gate is missing (as `allowlist-node` was), when someone deletes a
    workflow step to force a green, when a gate is neutered with `if: false`, and
    when one is planted in the wrong job — see
    `_gateway_unconditional_run_steps`. The step must also invoke the gate as its
    OWN command, so an incidental mention in a comment or a longer command line
    does not count.
    """
    steps = _gateway_unconditional_run_steps()
    invoked = {
        line.strip()
        for step in steps
        for raw in step.splitlines()
        if (line := raw.split("#", 1)[0]).strip()
    }
    missing = [
        gate
        for gate in _make_ci_prerequisites()
        if not any(inv.startswith(f"make {gate}") for inv in invoked) and gate not in _SHARDED_GATES
    ]
    assert not missing, (
        f"`make ci` runs {missing} but the gateway job never invokes them as an "
        "unconditional step — these gates are unenforced in CI"
    )


def test_the_sharded_test_gate_is_enforced_in_full() -> None:
    """`make ci`'s `test-ci` is satisfied by shards PLUS a combined coverage gate.

    `test-ci` itself is deliberately NOT in the gateway job any more: the suite is split
    across a shard matrix (`make test-ci-shard`), because one runner needed 65-82 min. That
    substitution is only legitimate if BOTH halves are present, so both are asserted here
    rather than left to the exemption in `_SHARDED_GATES`:

    * every shard runs the tests, and
    * a separate job re-imposes the 80% coverage threshold on the COMBINED data.

    Without the second half the shards would each carry `--cov-fail-under=0` and the coverage
    gate would have silently evaporated — a gate relocated is a gate that needs re-proving.
    """
    gateway_steps = " ".join(_gateway_unconditional_run_steps())
    assert "make test-ci-shard" in gateway_steps, (
        "the gateway job must run `make test-ci-shard`; `test-ci` is exempted from the gate "
        "parity check only because the sharded form replaces it"
    )

    workflow = _load(CI_WORKFLOW)
    matrix = workflow["jobs"]["gateway"].get("strategy", {}).get("matrix", {})
    shards = matrix.get("shard")
    assert isinstance(shards, list) and len(shards) > 1, (
        f"sharding needs a matrix of >1 shard; got {shards!r}"
    )

    coverage_job = workflow["jobs"].get("coverage")
    assert coverage_job is not None, (
        "no `coverage` job — the 80% threshold the shards skip has nowhere to be enforced"
    )
    coverage_runs = " ".join(
        str(step["run"]) for step in coverage_job.get("steps", []) if step.get("run")
    )
    assert "make coverage-combine" in coverage_runs, (
        "the `coverage` job must run `make coverage-combine`, which is what re-imposes "
        "--cov-fail-under=80 across the combined shard data"
    )


def test_the_shard_count_matches_the_matrix_exactly() -> None:
    """The declared shard TOTAL must equal the number of matrix jobs, and cover 1..N.

    This closes a silent-test-loss hole. The shard total lives in the Tests step's `SHARDS`
    env while the job list lives in `strategy.matrix.shard`, so the two can drift — and the
    two directions of drift are NOT symmetric:

    * SHARDS < len(matrix): the extra jobs pass an out-of-range index to tests/_shard.py,
      which RAISES. Loud, self-announcing, harmless.
    * SHARDS > len(matrix): every job that DOES exist gets a valid index and passes, while
      the tests belonging to the missing indices are simply never run anywhere. Six green
      shards, a green `ci`, and a slice of the suite silently unexecuted.

    The second is the masked-gate shape this repo keeps rediscovering (todos #107/#108/#109):
    a check that never reaches a verdict reports green. Nothing else catches it — the
    partition property in tests/repo_hygiene/test_shard_partition.py holds *for the N it is
    given*, and each individual shard is genuinely correct. Only the cross-check between the
    two declarations can see it.

    Also pins the values to exactly 1..N: `_shard_config` requires `1 <= index <= total`, so
    a matrix of [0, 1, 2] or [1, 1, 2] is either an immediate error or a double-run with a
    permanently unexecuted slice.
    """
    workflow = _load(CI_WORKFLOW)
    gateway = workflow["jobs"]["gateway"]
    shards = gateway.get("strategy", {}).get("matrix", {}).get("shard")
    assert isinstance(shards, list), f"no `strategy.matrix.shard` list; got {shards!r}"

    declared = [
        step["env"]["SHARDS"]
        for step in gateway.get("steps", [])
        if isinstance(step.get("env"), dict) and "SHARDS" in step["env"]
    ]
    assert len(declared) == 1, (
        f"expected exactly one step declaring a SHARDS env var, found {len(declared)}: "
        f"{declared!r}. More than one is drift waiting to happen; zero means the shard "
        f"total is coming from somewhere this guard cannot see."
    )
    total = int(declared[0])

    assert total == len(shards), (
        f"SHARDS={total} but the matrix has {len(shards)} jobs ({shards!r}). If SHARDS is "
        f"the larger of the two, the tests assigned to the missing shard indices are never "
        f"run by ANY job and the whole pipeline still reports green."
    )
    assert sorted(int(s) for s in shards) == list(range(1, total + 1)), (
        f"matrix.shard must be exactly 1..{total} with no gaps or duplicates; got {shards!r}"
    )


def test_every_gate_bearing_job_is_required_by_the_aggregate_gate() -> None:
    """The `ci` job must depend on every job that can fail, or that job gates nothing.

    A shard matrix turns `gateway` into one status context PER SHARD, so branch protection points at
    one aggregate context (`ci`) instead of a list that must be re-edited whenever the shard
    count changes. That indirection is only safe while `ci` actually depends on everything:
    a job missing from `needs` is a job whose failure cannot block a merge, which is the
    job-level form of the masked gate this repo keeps finding.

    Also asserts `ci` runs unconditionally (`if: always()`). With a bare `needs:` it would be
    SKIPPED when a dependency fails, and a skipped required check is not a failing one.
    """
    workflow = _load(CI_WORKFLOW)
    jobs = workflow["jobs"]
    aggregate = jobs.get("ci")
    assert aggregate is not None, "no aggregate `ci` job for branch protection to require"

    needs = aggregate.get("needs", [])
    needs = [needs] if isinstance(needs, str) else list(needs)

    expected = {name for name in jobs if name != "ci"}
    missing = sorted(expected - set(needs))
    assert not missing, (
        f"the `ci` gate does not depend on {missing} — a job outside its `needs` cannot "
        "block a merge, so its failure would be invisible to branch protection"
    )

    assert str(aggregate.get("if", "")).strip() == "always()", (
        "the `ci` job must be `if: always()`; otherwise a failed dependency SKIPS it, and a "
        "skipped required check does not block a merge"
    )


def test_gateway_timeout_outlasts_the_suite() -> None:
    """The gateway job must be allowed to finish the work it runs.

    Since PR #104 that work is ONE SHARD, not the whole suite, so the floor tracks a
    shard's measured duration — see MIN_GATEWAY_TIMEOUT_MINUTES for why re-deriving it
    downward tightened this bound rather than relaxing it.

    A `timeout-minutes` below that wall-clock is not a safety bound, it is a guaranteed
    `cancelled`: the runner kills the job mid-run and the check reports neither pass nor
    fail. This repo has now proven that twice — at 30 min (every run on `main` from
    2026-07-23) and again at 75.

    This is the same family as the other guards in this module — a gate that
    looks enforced and enforces nothing — so it is asserted here rather than
    left to a comment someone tunes away to save metered minutes.

    `isinstance(True, int)` is True in Python, so a `timeout-minutes: true`
    typo passes the type check and is caught by the floor comparison below
    (`True >= 60` is False) — wrong message, right outcome: still fails closed.
    """
    timeout = _load(CI_WORKFLOW)["jobs"]["gateway"].get("timeout-minutes")
    assert isinstance(timeout, int), (
        f"ci.yml gateway job has no integer `timeout-minutes` (got {timeout!r}). "
        "An unbounded job can wedge a runner; an unparseable one cannot be checked."
    )
    assert timeout >= MIN_GATEWAY_TIMEOUT_MINUTES, (
        f"ci.yml gateway `timeout-minutes` is {timeout}, below the "
        f"{MIN_GATEWAY_TIMEOUT_MINUTES}-minute floor. Measured: the serial suite "
        "was still running at 74m17s on this runner. At this budget the job is "
        "cancelled mid-suite and the check never reaches a verdict."
    )


def test_ci_python_version_is_patch_pinned_and_matches_dev() -> None:
    """CI and dev must run the SAME Python, down to the patch.

    Not hygiene — a correctness gate. `ipaddress` special-purpose network lists
    changed in a 3.12 PATCH release (CVE-2024-4032 / gh-113171), and the egress
    SSRF policy reads those predicates:

        ipaddress.ip_address("::ffff:10.20.30.40").is_reserved
            3.12.3  -> True      (denied before the RFC1918 allow-list rescue runs)
            3.12.13 -> False     (reaches the rescue, allowed)

    CI pinned only "3.12", resolved 3.12.3, and failed
    test_write_time_opt_in_relaxes_only_private_class in run 31243949907 while the
    same code passed on a dev box. A security control whose verdict depends on the
    interpreter's patch version is not reproducible, so both ends are pinned and
    checked against each other here. See todo #97.
    """
    pinned = (REPO_ROOT / "apps" / "gateway" / ".python-version").read_text().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned), (
        f".python-version is {pinned!r}; it must name an exact patch version "
        "(e.g. 3.12.13), not a minor series — a minor series is what let CI drift "
        "to 3.12.3 while dev ran 3.12.13."
    )
    ci_version = str(_load(CI_WORKFLOW)["jobs"]["gateway"]["steps"][1]["with"]["python-version"])
    assert ci_version == pinned, (
        f"ci.yml pins Python {ci_version!r} but .python-version says {pinned!r}. "
        "These must agree exactly — `ipaddress` predicates the egress policy relies "
        "on differ across 3.12 patch releases."
    )


def test_kind_e2e_is_dispatch_only() -> None:
    """`kind-e2e` must not report a status on pull requests.

    `ci-restoration` CR v2 (Tin-approved) settled this: kind-e2e "stays opt-in
    (`workflow_dispatch`) and runs before a release cut". It is a 45-minute job
    against the metered-minutes budget with 0 green runs in 15 attempts, and its
    own header comment already calls it "opt-in by design" — but its `on:` block
    still carried `pull_request` with an `apps/**` path filter, which matches
    essentially every PR in this repo.

    A permanently-red check on every PR is worse than no check: it trains every
    reviewer to ignore the checks that DO mean something.
    """
    triggers = _triggers(_load(KIND_E2E_WORKFLOW))
    assert set(triggers) == {"workflow_dispatch"}, (
        f"kind-e2e triggers on {sorted(map(str, triggers))}, expected only "
        "['workflow_dispatch']. Per ci-restoration CR v2 it is opt-in and runs "
        "before a release cut — a PR trigger makes it a permanently-red gate."
    )


def test_main_runs_are_never_cancelled_by_the_concurrency_group() -> None:
    """A `concurrency` group must exist, and it must NEVER cancel a run on `main`.

    The group itself is a slot-starvation fix: one run is 5 jobs (4 shards + dashboard)
    and the Free plan cap is 5 CONCURRENT jobs account-wide, so two runs do not overlap —
    they serialize. Without cancellation, every re-push to a PR branch left the previous
    run's 5 jobs occupying every slot while the new one waited (observed 2026-08-11: main's
    push run for 9279c51 sat `queued` behind PR #102's run).

    The asymmetry is the whole point of this guard. `cancel-in-progress: true` is the
    obvious "simplification", and it would make MAIN runs cancellable — turning the
    release-integrity record into a `cancelled` check that reaches no verdict. That is the
    precise fault this milestone closed, after three caps produced three runs that proved
    nothing. A cancelled required check is not a failing one, so it does not even announce
    itself as a regression.

    So this asserts the exclusion by BEHAVIOUR, not by matching a literal string: the
    expression must evaluate falsey for `refs/heads/main` and truthy for a PR ref.
    """
    workflow = _load(CI_WORKFLOW)
    concurrency = workflow.get("concurrency")
    assert isinstance(concurrency, dict), (
        f"ci.yml has no workflow-level `concurrency` block (got {concurrency!r}). Without "
        "one, a superseded run keeps all 5 concurrency slots until it finishes."
    )

    group = str(concurrency.get("group", ""))
    assert "github.ref" in group, (
        f"`concurrency.group` is {group!r} and does not vary by `github.ref`. A constant "
        "group serializes EVERY branch against every other — and would let a PR push "
        "cancel a main run outright."
    )

    raw = concurrency.get("cancel-in-progress")
    assert raw is not True, (
        "`cancel-in-progress: true` cancels MAIN runs too. A cancelled run on main is a "
        "required check that reaches no verdict — the exact fault R6 closed. Use an "
        "expression that excludes refs/heads/main."
    )
    assert raw not in (None, False), (
        f"`cancel-in-progress` is {raw!r}, so superseded PR runs are never cancelled and "
        "keep holding all 5 concurrency slots — the starvation this block exists to fix."
    )

    expression = str(raw).strip().removeprefix("${{").removesuffix("}}").strip()
    comparison = re.fullmatch(r"github\.ref\s*(==|!=)\s*'([^']*)'", expression)
    assert comparison is not None, (
        f"`cancel-in-progress` is {expression!r}, which this guard cannot evaluate. Keep it "
        f"to the form `github.ref != 'refs/heads/main'` so the main-exclusion stays "
        f"mechanically checkable rather than resting on a comment."
    )
    operator, literal = comparison.group(1), comparison.group(2)

    def cancels(ref: str) -> bool:
        return ref != literal if operator == "!=" else ref == literal

    # Assert the SEMANTICS for the only two refs that matter, not a literal string, so any
    # equivalent spelling passes and any main-cancelling one fails.
    for ref, must_cancel in (("refs/heads/main", False), ("refs/pull/123/merge", True)):
        assert cancels(ref) is must_cancel, (
            f"`cancel-in-progress` evaluates to {cancels(ref)} for {ref!r}, expected "
            f"{must_cancel}. Main must never be cancelled (it is the release-integrity "
            f"record); PR refs must be, or superseded runs starve live ones."
        )
