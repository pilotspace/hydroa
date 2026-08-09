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

# The gateway job's wall-clock floor, set from a MEASUREMENT rather than an
# extrapolation. Two runs were cancelled proving the point: at 30 min (every run on
# main from 2026-07-23) and again at 75 (run 31197251730, 2026-08-07 — 74m17s with
# the serial suite still going). Steps 1-9 cost 32s combined, so the budget is
# essentially all test time.
#
# MEASURED, at last. Run 31243949907 (2026-08-08) is the first gateway run that
# ever FINISHED: 4553 passed / 8 failed in 4935s = 1h22m15s, at -n 4 with coverage.
# This floor is that number x ~1.5. Three earlier caps were guessed and all three
# were cancelled mid-suite (30 -> ~30m, 75 -> 74m17s serial, 60 -> 59m27s at -n 4),
# so every figure before this one was a lower bound, never a runtime.
#
# Re-derive this constant from an observed run, never from an extrapolation: the
# 75 came from scaling a 12-core dev-host number to a 4-core runner and was wrong,
# and the 60 that replaced it was wrong too. Both mistakes cost a cancelled run.
#
# A cap below the suite's own runtime is not a safety bound — it is a guaranteed
# `cancelled`, a check that never reaches a verdict, and a gate that proves nothing.
MIN_GATEWAY_TIMEOUT_MINUTES = 120


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
    missing = [gate for gate in _make_ci_prerequisites() if f"make {gate}" not in invoked]
    assert not missing, (
        f"`make ci` runs {missing} but the gateway job never invokes them as an "
        "unconditional step — these gates are unenforced in CI"
    )


def test_gateway_timeout_outlasts_the_suite() -> None:
    """The gateway job must be allowed to finish the suite it runs.

    A `timeout-minutes` below the suite's own wall-clock is not a safety bound,
    it is a guaranteed `cancelled`: the runner kills the job mid-suite and the
    check reports neither pass nor fail. This repo has now proven that twice —
    at 30 min (every run on `main` from 2026-07-23) and again at 75.

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
    ci_version = str(
        _load(CI_WORKFLOW)["jobs"]["gateway"]["steps"][1]["with"]["python-version"]
    )
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
