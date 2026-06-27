"""Structural red/green tests for v53 task 10 (ci-e2e-pipeline §1–§3).

These assert the SHAPE of three NEW artifacts — the kind-in-CI workflow, the `make ci-e2e`
aggregate target, and the cloud-deploy runbook — plus an additive-guard on the existing ci.yml
and a no-secret-inlined scan. They are config/doc-structure tests (like `tests/helm/`), NOT the
live e2e: the heavy `make ci-e2e` run is the verify-phase RUNTIME evidence (out of the default
suite, mirroring how the kind_e2e suites stay out of the default pytest run).

Red-for-the-right-reason: before Build, `.github/workflows/kind-e2e.yml` + `docs/runbooks/
cloud-deploy.md` + the `ci-e2e` Makefile target do not exist, so every assertion that reads them
fails (FileNotFound / missing key / missing target). `ci.yml` DOES exist, so the additive-guard
test (`test_existing_ci_unchanged`) is GREEN even pre-build — an intentional anchor proving the
guard can never pass vacuously.

Note: under YAML 1.1 the GitHub `on:` key parses to the boolean True; we read triggers via
`_on(doc)` rather than `doc["on"]`, and otherwise lean on raw-text checks where that is more robust.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
KIND_WF = WORKFLOWS / "kind-e2e.yml"
CI_WF = WORKFLOWS / "ci.yml"
MAKEFILE = REPO / "Makefile"
RUNBOOK = REPO / "docs" / "runbooks" / "cloud-deploy.md"

# The sole pre-existing fake secret (kind throwaway Fernet) — must NOT leak into the new artifacts.
KIND_FERNET = "ykjz8nXk81rGbmRCVIhK0HDxss5FYDHQxLqa4C_81N4="
# A Fernet/base64 key shape (44 chars, trailing '='): a high-signal "real secret" tell.
SECRET_SHAPE = re.compile(r"[A-Za-z0-9_\-]{43}=")
PINNED_USES = re.compile(r"@(v\d+|[0-9a-f]{40})$")


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def _load(p: pathlib.Path) -> dict:
    return yaml.safe_load(_read(p))


def _steps(job: dict) -> list[dict]:
    return [s for s in job.get("steps", []) if isinstance(s, dict)]


def _all_runs(job: dict) -> str:
    return "\n".join(str(s.get("run", "")) for s in _steps(job))


# ── M1: the workflow drives the full kind e2e + always-teardown ──────────────────────────────
def test_workflow_runs_full_kind_e2e() -> None:
    doc = _load(KIND_WF)
    jobs = doc.get("jobs", {})
    assert jobs, "kind-e2e.yml must define at least one job"
    job = next(iter(jobs.values()))
    runs = _all_runs(job)
    # the heavy pipeline is driven by the aggregate target (which fans out to kind-up + both suites)
    assert "make ci-e2e" in runs, f"a run step must invoke `make ci-e2e`; got:\n{runs}"
    # unconditional teardown: an `if: always()` step that tears the cluster down
    teardown = [
        s
        for s in _steps(job)
        if "always()" in str(s.get("if", "")) and "kind-down" in str(s.get("run", ""))
    ]
    assert teardown, "an `if: always()` step must run `make kind-down` (never leave a cluster up)"


# ── M2: well-formed + pinned + bounded ───────────────────────────────────────────────────────
def test_workflow_is_wellformed_and_pinned() -> None:
    doc = _load(KIND_WF)
    assert isinstance(doc, dict), "kind-e2e.yml must parse to a mapping"
    assert doc.get("permissions", {}).get("contents") == "read", "permissions.contents must be read"
    job = next(iter(doc.get("jobs", {}).values()))
    timeout = job.get("timeout-minutes")
    assert isinstance(timeout, int) and timeout > 0, "the job must declare a bounded timeout-minutes"
    uses = [s["uses"] for s in _steps(job) if s.get("uses")]
    assert uses, "the job must use at least one action (checkout)"
    unpinned = [u for u in uses if not PINNED_USES.search(u)]
    assert not unpinned, f"every `uses:` must be version/SHA pinned; unpinned: {unpinned}"


# ── M2/R2: the existing CI is untouched (ANCHOR — green even pre-build) ───────────────────────
def test_existing_ci_unchanged() -> None:
    # BYTE-IDENTITY guard (hardened per the t8-style gate-harden): the working-tree ci.yml must
    # match the committed HEAD blob EXACTLY — stronger than "the two jobs still exist", so it
    # catches any edit (a slipped-in step, a changed trigger), not just a job removal.
    head = subprocess.run(
        ["git", "show", "HEAD:.github/workflows/ci.yml"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    assert head.returncode == 0, f"could not read ci.yml at HEAD: {head.stderr}"
    assert _read(CI_WF) == head.stdout, "ci.yml differs from HEAD — t10 must be additive (no ci.yml edit)"
    # keep the structural anchor too: the two jobs remain defined (the suite-runs proof)
    jobs = _load(CI_WF).get("jobs", {})
    assert "gateway" in jobs and "dashboard" in jobs, "ci.yml must still define gateway + dashboard jobs"


# ── M3: the local mirror target exists ───────────────────────────────────────────────────────
def test_ci_e2e_target_exists() -> None:
    text = _read(MAKEFILE)
    assert re.search(r"^ci-e2e:", text, re.M), "Makefile must declare a `ci-e2e:` target"
    phony = "\n".join(line for line in text.splitlines() if ".PHONY" in line)
    assert "ci-e2e" in phony, "`ci-e2e` must be listed in .PHONY"
    # the recipe block (indented lines after `ci-e2e:`) must drive BOTH e2e suites
    block = re.search(r"^ci-e2e:.*?(?=^\S|\Z)", text, re.M | re.S)
    assert block, "could not isolate the ci-e2e recipe block"
    recipe = block.group(0)
    assert "e2e_kind.sh" in recipe, "ci-e2e recipe must run the API e2e (scripts/e2e_kind.sh)"
    assert "e2e_kind_ui.sh" in recipe, "ci-e2e recipe must run the browser e2e (scripts/e2e_kind_ui.sh)"


# ── M4: the deploy runbook documents the real-cloud apply ────────────────────────────────────
def test_runbook_has_required_sections() -> None:
    text = _read(RUNBOOK)
    low = text.lower()
    assert "prerequisite" in low, "runbook must document prerequisites"
    assert "helm upgrade --install" in text, "runbook must show the helm upgrade --install apply"
    assert "values-prod.yaml" in text, "runbook must reference the values-prod overlay (the swap)"
    assert ("kubectl create secret" in text) or ("secret" in low), "runbook must cover secret population"
    assert "verify" in low, "runbook must have a post-apply verification section"
    assert "backup-rollback.md" in text, "runbook must point at the rollback runbook"


# ── M5/R3: the HARD-STOP boundary + the NetworkPolicy pre-apply gate ──────────────────────────
def test_runbook_marks_hardstop_and_np_gate() -> None:
    text = _read(RUNBOOK)
    low = text.lower()
    assert ("human-run" in low) or ("human run" in low) or ("manually" in low), (
        "runbook must mark the apply as human-run"
    )
    assert "ci" in low and ("never" in low or "not" in low), "runbook must state the apply is not CI-executed"
    assert "networkpolic" in low, "runbook must name the NetworkPolicy pre-apply gate"
    assert "enforc" in low, "runbook must call out NetworkPolicy enforcement (kind disables NPs)"
    assert "encryption" in low, "runbook must list the enc-key fail-fast open item"


# ── R1: no secret value inlined in the new artifacts ─────────────────────────────────────────
def test_no_secret_inlined() -> None:
    for p in (KIND_WF, RUNBOOK):
        text = _read(p)
        assert KIND_FERNET not in text, f"{p.name} leaks the kind throwaway Fernet key"
        shaped = SECRET_SHAPE.findall(text)
        assert not shaped, f"{p.name} contains a secret-shaped literal: {shaped!r}"
