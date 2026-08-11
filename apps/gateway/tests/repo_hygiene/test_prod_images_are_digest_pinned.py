"""Every image the PRODUCTION stack runs must be pinned by digest.

Why this is a standing guard and not a one-time fix (todo #113): the images floated for the
project's whole life and nothing noticed, because a floating tag is invisible until the day
it resolves somewhere new. The pin is one line; keeping the pin is the hard part, and
"just use the tag, it's easier to read" is a very natural change to make.

The Postgres pin is the load-bearing one, and it is a DATA-LOSS control rather than hygiene.
`pg16` is a floating MINOR: an unattended `docker compose pull` can swap the image's libc
base under a live data volume, and a glibc<->musl change alters the collation version the
cluster was built with. The 2026-08-10 runbook walk (PR #102) confirmed both halves
empirically — indexes corrupt SILENTLY, and the documented same-volume remedy does not work
(`REFRESH COLLATION VERSION` errors with `invalid collation version change`), so the
preflight can never be cleared in place and recovery is dump/restore. A routine pull can
therefore produce a database that is unrecoverable where it sits.

Envoy is the second-most important: `v1.29-latest` floats BY NAME on the component that
terminates TLS and enforces ext_authz, so an unattended pull changes the security boundary
itself with no review and no record.

Scope is deliberately PRODUCTION ONLY. Dev and e2e compose stacks are rebuilt constantly and
a float there costs a re-pull, not a database; pinning them would add friction with no
corresponding risk reduction. If that judgement changes, widen `_COMPOSE_FILES` — do not
weaken the assertion.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
_COMPOSE_FILES = (REPO_ROOT / "infra" / "docker-compose.prod.yml",)

# `name@sha256:<64 hex>`; the human-readable tag may sit in between and is encouraged.
_DIGEST_PINNED = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")

# A fully parameterised image is NOT a float: the value is supplied at deploy time from the
# release tag (docs/runbooks/cloud-deploy.md builds and pushes FROM THE TAG), so pinning it
# here would be pinning the wrong thing. A PARTIAL interpolation like `myrepo:${TAG}` is a
# different animal and is not accepted below — it hides a mutable tag behind a variable.
_FULLY_PARAMETERISED = re.compile(r"^\$\{[A-Z_][A-Z0-9_]*\}$")


def _services(path: Path) -> dict[str, Any]:
    compose = cast(dict[str, Any], yaml.safe_load(path.read_text()))
    return cast(dict[str, Any], compose.get("services", {}))


def test_every_production_image_is_digest_pinned() -> None:
    """No production service may run a mutable reference.

    Fails on a bare tag (`pgvector/pgvector:pg16`), on `:latest`, and on a partially
    interpolated reference — each of which lets the running bits change with no commit.
    """
    floating: list[str] = []
    checked = 0

    for path in _COMPOSE_FILES:
        assert path.exists(), f"{path} is missing — this guard is checking nothing"
        for name, service in _services(path).items():
            image = service.get("image")
            if image is None:
                continue  # a `build:`-only service has no registry reference to pin
            image = str(image).strip()
            checked += 1
            if _FULLY_PARAMETERISED.match(image) or _DIGEST_PINNED.match(image):
                continue
            floating.append(f"{path.name}::{name} -> {image}")

    assert checked, (
        "no images found in the production compose file(s) — the guard is vacuous, which is "
        "worse than absent because it reports green while checking nothing"
    )
    assert not floating, (
        "production services must pin images by digest (`repo:tag@sha256:...`), or be fully "
        "parameterised (`${VAR}`) and supplied from the release tag at deploy time.\n  "
        + "\n  ".join(floating)
        + "\n\nA floating tag lets an unattended `docker compose pull` change the running "
        "bits with no commit and no review. For Postgres specifically this is a DATA-LOSS "
        "risk, not hygiene: a libc base change alters the collation version under a live "
        "volume, corrupting indexes SILENTLY, and `REFRESH COLLATION VERSION` cannot clear "
        "it in place (proved on a real volume, PR #102) — recovery is dump/restore.\n"
        "Resolve a digest with `docker buildx imagetools inspect <tag>` and confirm it is a "
        "multi-arch LIST carrying linux/amd64 before committing it. See todo #113."
    )


def test_the_postgres_pin_keeps_its_readable_tag() -> None:
    """The Postgres pin must carry `pg16` alongside the digest.

    A bare `pgvector/pgvector@sha256:...` is equally immutable but tells a reader nothing
    about which major version is deployed — and the major version is exactly what someone
    reaches for this file to check during an incident. Cheap to keep, expensive to miss.
    """
    services = _services(REPO_ROOT / "infra" / "docker-compose.prod.yml")
    image = str(services["postgres"]["image"])
    assert "pgvector/pgvector:pg16@sha256:" in image, (
        f"the production Postgres image is {image!r}. Keep the readable `:pg16` tag next to "
        f"the digest so the deployed major version is legible at a glance; the digest is "
        f"what resolves, the tag is what a human reads during an incident."
    )
