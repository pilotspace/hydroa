"""A published release image must run on the cluster's architecture, and that must be PROVEN.

Todo #117/#118. The defect this exists for was found by RUNNING the release runbook on
2026-08-12: `docker build` with no `--platform` produces an image for the OPERATOR'S HOST
arch. On an Apple-silicon laptop both release images came out `linux/arm64`. Pushed to a
typical amd64 node pool, that image fails with `exec format error` — at DEPLOY time, long
after the artifact was published and the tag already means the wrong thing.

Two properties are asserted, and the second is the one with teeth:

  1. every place that builds a RELEASE image asks for both architectures;
  2. the publish workflow VERIFIES the pushed manifest actually lists them.

(2) matters because (1) is not self-enforcing. `docker buildx build --platform a,b` without
`--push` writes only the host arch into the local store — the multi-arch flag is silently
partial, the command still exits 0, and a plain `docker push` afterwards uploads one arch.
"the build succeeded" is therefore NOT evidence that the right artifact exists in the
registry. Only inspecting the published manifest is. This is the same masked-gate shape the
repo keeps finding: a step that cannot reach the verdict it appears to give.

Scope is deliberately RELEASE artifacts. kind/e2e builds are loaded straight into the local
cluster with `kind load` and never leave the runner, so their arch is the runner's by
definition and a platform flag there would add cost for no risk reduction.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
PUBLISH_WORKFLOW = WORKFLOW_DIR / "publish-images.yml"
DEPLOY_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "cloud-deploy.md"

# A build line naming one of these images is building a RELEASE artifact; anything else
# (kind, local dev) is out of scope. Keyed on the IMAGE NAME, not the registry: the runbook
# writes `<registry>/ai-proxy-gateway` with a deliberate placeholder — the registry is an
# operator input and does not live in this repo — while the workflow writes the real
# ghcr.io host. Keying on the registry matched the workflow and MISSED the runbook, which is
# where the original defect actually lived. The vacuity test below is what caught that.
RELEASE_IMAGES = ("ai-proxy-gateway", "ai-proxy-dashboard")

_BUILD_LINE = re.compile(r"\bdocker\s+(buildx\s+)?build\b")
_PLATFORM_FLAG = re.compile(r"--platform[= ]([^\s\\]+)")

REQUIRED_ARCHES = ("linux/amd64", "linux/arm64")


def _logical_lines(text: str) -> list[str]:
    """Join shell backslash-continuations so a flag on the next line still counts.

    Both surfaces wrap long docker invocations across lines. Reading the file line-by-line
    would see `docker buildx build \\` with no `--platform` on it and report a defect that
    is not there — a guard that cries wolf gets deleted rather than obeyed.
    """
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    return [line.strip() for line in joined.splitlines() if line.strip()]


def _release_build_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [
        line
        for line in _logical_lines(path.read_text(encoding="utf-8"))
        if _BUILD_LINE.search(line) and any(image in line for image in RELEASE_IMAGES)
    ]


def _surfaces() -> dict[str, list[str]]:
    """Every file that could build a release image, mapped to its build lines."""
    surfaces = {str(DEPLOY_RUNBOOK.relative_to(REPO_ROOT)): _release_build_lines(DEPLOY_RUNBOOK)}
    for workflow in sorted(WORKFLOW_DIR.glob("*.yml")):
        surfaces[str(workflow.relative_to(REPO_ROOT))] = _release_build_lines(workflow)
    return surfaces


# ─────────────────────────────────────────────────────────────────────────────
# Vacuity — a guard that polices nothing reports green forever
# ─────────────────────────────────────────────────────────────────────────────


def test_the_guard_can_see_a_release_build_at_all() -> None:
    """If no surface matches, every assertion below is vacuously true.

    This is the failure mode that let the original defect ship: nothing was looking. A
    registry rename, a runbook restructure, or a move to a composite action would all
    silently empty the population — so emptiness is a FAILURE, not a pass.
    """
    surfaces = _surfaces()
    total = sum(len(lines) for lines in surfaces.values())

    assert total > 0, (
        "no release-image build command found in any known surface — this guard is asserting "
        f"over an empty set and would stay green through any regression. Searched for "
        f"{list(RELEASE_IMAGES)} in {sorted(surfaces)}. If builds moved, point the guard at "
        "the new surface; do NOT delete this test."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 1 — every release build asks for both architectures
# ─────────────────────────────────────────────────────────────────────────────


def test_every_release_build_declares_both_architectures() -> None:
    offenders: list[str] = []
    for surface, lines in _surfaces().items():
        for line in lines:
            match = _PLATFORM_FLAG.search(line)
            if match is None:
                offenders.append(
                    f"{surface}: no --platform (builds the host arch only)\n    {line[:150]}"
                )
                continue
            declared = match.group(1).split(",")
            missing = [arch for arch in REQUIRED_ARCHES if arch not in declared]
            if missing:
                offenders.append(f"{surface}: --platform is missing {missing}\n    {line[:150]}")

    assert not offenders, (
        "a release image would be published for the wrong architecture:\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\n\nA bare `docker build` produces the OPERATOR'S host arch. Measured 2026-08-12: "
        "on Apple silicon both release images built linux/arm64, which dies with 'exec format "
        "error' on an amd64 node pool AFTER publication. Use:\n"
        "    docker buildx build --platform linux/amd64,linux/arm64 ... --push"
    )


def test_a_multi_arch_build_also_pushes() -> None:
    """`--platform a,b` without `--push` silently degrades to the host arch.

    buildx cannot store a manifest list in the local docker image store, so it keeps only
    the host arch and still exits 0. The flag then reads as multi-arch protection while
    providing none — which is worse than not having it, because it stops people looking.
    """
    offenders = [
        f"{surface}: --platform without --push\n    {line[:150]}"
        for surface, lines in _surfaces().items()
        for line in lines
        if _PLATFORM_FLAG.search(line) and "--push" not in line
    ]

    assert not offenders, (
        "multi-arch build that does not push:\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\n\n`--push` is REQUIRED for a manifest list to reach the registry. Without it "
        "buildx keeps the host arch only and a later `docker push` uploads that one arch."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 2 — publication is VERIFIED, not assumed
# ─────────────────────────────────────────────────────────────────────────────


def test_a_publish_workflow_exists() -> None:
    """Hand-built release images are how the arch defect reached a published tag.

    While publication is a human running docker on a laptop, there is nowhere to attach the
    verification below, and correctness rests on whoever ran the command.
    """
    assert PUBLISH_WORKFLOW.is_file(), (
        f"{PUBLISH_WORKFLOW.relative_to(REPO_ROOT)} does not exist — release images are still "
        "hand-built (todo #118). Nothing enforces the multi-arch fix without a publish step."
    )


def test_the_publish_workflow_verifies_the_pushed_manifest() -> None:
    """A green build job is not evidence the registry holds the right artifact.

    The job must read the manifest BACK from the registry and fail if an architecture is
    absent. Without this the workflow reports success for a single-arch push, which is
    exactly the defect it was written to prevent.
    """
    assert PUBLISH_WORKFLOW.is_file(), "publish workflow missing — see the previous test"
    body = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    assert "imagetools inspect" in body, (
        "the publish workflow never inspects the pushed manifest. `docker buildx imagetools "
        "inspect <ref>` is what turns 'the command exited 0' into 'the registry actually "
        "holds an amd64 image'."
    )
    for arch in REQUIRED_ARCHES:
        assert arch in body, (
            f"the publish workflow never names {arch}, so it cannot be checking that the "
            f"published manifest contains it."
        )


def test_the_verification_can_fail_the_job() -> None:
    """A check whose result is discarded is the masked-gate shape, not a check.

    `imagetools inspect | grep amd64` only guards anything if a missing arch makes the step
    exit non-zero. Piping into grep does that; `|| true`, `continue-on-error`, or a bare
    print does not.
    """
    assert PUBLISH_WORKFLOW.is_file(), "publish workflow missing — see the earlier test"
    body = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    verify_lines = [line for line in _logical_lines(body) if "imagetools inspect" in line]
    assert verify_lines, "no imagetools inspect line found"

    neutered = [line for line in verify_lines if "|| true" in line or "continue-on-error" in line]
    assert not neutered, (
        "the manifest verification cannot fail the job:\n"
        + "\n".join(f"  - {line[:150]}" for line in neutered)
        + "\n\nA verification that swallows its own verdict reports green through the exact "
        "regression it exists to catch."
    )

    assert "continue-on-error" not in body, (
        "continue-on-error appears in the publish workflow — a publish step that is allowed "
        "to fail silently can leave a tag pointing at nothing, or at one architecture."
    )
