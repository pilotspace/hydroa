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
COMPOSE_DEV = REPO_ROOT / "infra" / "docker-compose.dev.yml"
CHART_VALUES = REPO_ROOT / "charts" / "ai-proxy" / "values.yaml"
MAKEFILE = REPO_ROOT / "Makefile"

# The exact image the project deploys. Bumping Postgres/pgvector means bumping
# this line AND all four manifests together — which is the whole point.
PGVECTOR_IMAGE = "pgvector/pgvector:pg16"


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text()))


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
