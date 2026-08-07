"""Failing-first (RED) suite — one version, declared once, served honestly.

release-provenance PLAN.md §4. Closes todo #86.

RED reason expected (before Build): `gateway.__version__` does not exist, and
`main.py` hardcodes `version="0.1.0"` in the `FastAPI(...)` construction.

The defect: three independent answers to "what version is this?".
  * `pyproject.toml`      says 0.7.0
  * `/openapi.json`       says 0.1.0   (a literal in main.py, never touched since)
  * `RELEASES.md`         stops at 0.12.0, while 0.13.0 has shipped
  * `git tag`             has nine tags with four gaps
None of them agree, and the one a client actually SEES — the served OpenAPI document —
is the least correct of the four. Under SOC 2 CC8.1 you have to be able to say which
artifact ran; right now the artifact cannot say it about itself.

Two of the six cases below exist to stop this being "fixed" in a way that recreates it:
`test_main_does_not_hardcode_a_version` (edit the literal to match and the single source
quietly dies) and `test_version_comparison_is_pep440_not_lexicographic` (the very bug —
"0.9.0" > "0.10.0" as strings — could re-enter through the guard written to prevent it).

Tag backfill (M5) is deliberately NOT tested here: a shallow CI clone carries no tags, so
such a test would be green-because-absent. See §4's "NOT TESTED, BY DESIGN" note; the
evidence is `git tag --sort=v:refname` recorded at the gate.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

GATEWAY_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = GATEWAY_ROOT / "pyproject.toml"
MAIN_PY = GATEWAY_ROOT / "src" / "gateway" / "main.py"
RELEASES_MD = GATEWAY_ROOT.parents[1] / "RELEASES.md"


def _pyproject_version() -> str:
    return str(tomllib.loads(PYPROJECT.read_text())["project"]["version"])


def _parts(version: str) -> tuple[int, ...]:
    """PEP 440-ish release tuple. Deliberately NOT a string compare — see the M3 test."""
    return tuple(int(p) for p in version.split(".")[:3])


# ─────────────────────────────────────────────────────────────────────────────
# M1 — what is SERVED must be what is DECLARED
# ─────────────────────────────────────────────────────────────────────────────


async def test_openapi_version_matches_pyproject(client) -> None:  # type: ignore[no-untyped-def]
    """Asserted against the served document, never against main.py's source. covers: M1

    Reading the source would prove the literal was edited. Only the response proves a
    client is told the truth, which is the thing that has been false since 0.1.0.
    """
    response = await client.get("/openapi.json")
    assert response.status_code == 200

    served = response.json()["info"]["version"]
    declared = _pyproject_version()

    assert served == declared, (
        f"/openapi.json advertises {served!r} while pyproject declares {declared!r}. A "
        "client integrating against this gateway cannot tell which build they are talking "
        "to, and neither can an auditor."
    )


# ─────────────────────────────────────────────────────────────────────────────
# M2 — the single source must stay single
# ─────────────────────────────────────────────────────────────────────────────


def test_main_does_not_hardcode_a_version() -> None:
    """No `version="X.Y.Z"` literal in the FastAPI(...) construction. covers: M2

    Without this arm, the obvious "fix" for a future drift is to edit the literal until it
    matches — which passes the M1 test and silently restores the defect.
    """
    source = MAIN_PY.read_text()
    literals = re.findall(r'version\s*=\s*["\']\d+\.\d+', source)

    assert literals == [], (
        f"main.py still hardcodes a version literal ({literals}). The version must come "
        "from gateway.__version__, which reads the installed distribution metadata — one "
        "source, or it drifts again."
    )


def test_version_is_importable_without_installed_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source-tree path must not raise at import. covers: M2

    A gateway that cannot start when run from a checkout without an installed
    distribution would be a worse defect than the one being fixed. The fallback must
    equal pyproject's version — that is what makes the fallback safe rather than a
    fourth disagreeing answer.
    """
    import importlib
    import importlib.metadata

    def _not_found(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError("hydroa-gateway")

    monkeypatch.setattr(importlib.metadata, "version", _not_found)

    import gateway

    reloaded = importlib.reload(gateway)
    try:
        assert reloaded.__version__ == _pyproject_version(), (
            f"the no-metadata fallback is {reloaded.__version__!r} but pyproject says "
            f"{_pyproject_version()!r} — a fallback that disagrees is just a fourth "
            "version to keep in sync"
        )
    finally:
        monkeypatch.undo()
        importlib.reload(gateway)


# ─────────────────────────────────────────────────────────────────────────────
# M3 / M4 — the release record must not fall behind, and the guard must compare
#           versions the way humans mean, not the way strings sort
# ─────────────────────────────────────────────────────────────────────────────


def test_releases_md_newest_entry_is_not_behind_pyproject() -> None:
    """covers: M3, M4"""
    headings = re.findall(r"^##\s+(\d+\.\d+\.\d+)", RELEASES_MD.read_text(), re.MULTILINE)
    assert headings, f"no `## X.Y.Z` release headings found in {RELEASES_MD}"

    newest = max(headings, key=_parts)
    declared = _pyproject_version()

    assert _parts(newest) >= _parts(declared), (
        f"RELEASES.md stops at {newest} but pyproject declares {declared}. A shipped "
        "release with no notes is how 0.13.0 went unrecorded in the first place."
    )


def test_version_comparison_is_pep440_not_lexicographic() -> None:
    """The bug could re-enter through its own guard. covers: M3

    `"0.9.0" > "0.10.0"` is TRUE as strings. That is exactly why `git tag --sort=v:refname`
    output looked orderly while four releases were missing from it.
    """
    assert _parts("0.9.0") < _parts("0.10.0") < _parts("0.13.0")
    assert "0.9.0" > "0.10.0", (
        "if this ever fails, Python's string ordering changed and the rest of this "
        "reasoning needs rechecking — it is asserted to document WHY tuple comparison is "
        "required, not because string order is desirable"
    )
    assert _parts("0.10.0") > _parts("0.9.0"), "the guard must not sort lexicographically"


def test_dashboard_version_is_independent() -> None:
    """The guard must NOT couple the dashboard to the gateway. covers: M3

    Tin's call at freeze: a gateway patch release must not ship a phantom UI release.
    Gated so a later reader does not "helpfully" tie them together.
    """
    import json

    package_json = GATEWAY_ROOT.parent / "dashboard" / "package.json"
    if not package_json.exists():  # pragma: no cover - defensive
        pytest.skip("dashboard package.json not present in this checkout")

    dashboard_version = str(json.loads(package_json.read_text())["version"])
    gateway_version = _pyproject_version()

    assert _parts(dashboard_version) != _parts(gateway_version), (
        f"the dashboard ({dashboard_version}) and the gateway ({gateway_version}) now "
        "carry the same version. Tin's freeze call was that they stay independently "
        "versioned, so a gateway patch does not ship a phantom UI release. If someone "
        "deliberately unified them, that is a change request — not an edit to this test."
    )
