"""Failing-first (RED) suite for node-dep governance.

Scenarios covered (ops-hardening TASK.md §2):
  - Scenario: check_node_deps.py exits 0 on current package.json (all deps allowlisted)
  - Scenario: check_node_deps.py exits non-zero on un-allowlisted dep
  - Scenario: check_node_deps.py exits non-zero when allowlist file is missing
  - Scenario: make ci includes the allowlist-node target
  - Scenario: make allowlist-node target invokes check_node_deps.py

RED reason expected:
  - test_check_node_deps_script_exists: scripts/check_node_deps.py does not exist yet
    → AssertionError
  - test_check_node_deps_exits_zero_current: script missing → returncode != 0
  - test_check_node_deps_exits_nonzero_unlisted: script missing → FileNotFoundError
    reading package.json (PACKAGE_JSON_PATH resolved from wrong REPO_ROOT if wrong)
    OR script exits wrong code once script exists
  - test_check_node_deps_missing_allowlist: script missing → assert script exists FAILS first
  - test_makefile_ci_includes_allowlist_node: "allowlist-node" not yet in Makefile ci target
  - test_makefile_allowlist_node_target_invokes_script: allowlist-node target does not exist
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

# ── Repo root (5 parents up from tests/ops/): apps/gateway/tests/ops → ops → tests
#    → gateway → apps → ai-proxy  ──────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_node_deps.py"
ALLOWLIST_PATH = REPO_ROOT / ".add" / "node-dependencies.allowlist"
PACKAGE_JSON_PATH = REPO_ROOT / "apps" / "dashboard" / "package.json"
MAKEFILE_PATH = REPO_ROOT / "Makefile"


# ══════════════════════════════════════════════════════════════════════════
# check_node_deps.py: script exists
# ══════════════════════════════════════════════════════════════════════════


def test_check_node_deps_script_exists() -> None:
    """scripts/check_node_deps.py must exist.

    RED reason: script has not been created yet.
    """
    assert CHECK_SCRIPT.exists(), (
        f"scripts/check_node_deps.py not found at {CHECK_SCRIPT}. "
        "Create the script during build phase."
    )


# ══════════════════════════════════════════════════════════════════════════
# Exit 0 on clean package.json
# ══════════════════════════════════════════════════════════════════════════


def test_check_node_deps_exits_zero_current() -> None:
    """check_node_deps.py must exit 0 for the current package.json.

    RED reason: script does not exist yet → python3 <missing> exits 2
                → assert returncode == 0 FAILS.
    """
    result = subprocess.run(
        ["python3", str(CHECK_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"check_node_deps.py exited {result.returncode} for clean package.json.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}\n"
        "Script does not exist yet (RED expected)."
    )


# ══════════════════════════════════════════════════════════════════════════
# Exit non-zero on un-allowlisted dep
# ══════════════════════════════════════════════════════════════════════════


def test_check_node_deps_exits_nonzero_unlisted() -> None:
    """check_node_deps.py must exit non-zero when an un-allowlisted dep is present.

    Uses a temporary package.json with "some-evil-package" injected.
    RED reason: script does not exist yet → must assert script exists first.
    """
    # Prerequisite: script must exist for this test to be meaningful.
    # When RED, this assertion fires and surfaces the right reason.
    assert CHECK_SCRIPT.exists(), (
        f"scripts/check_node_deps.py not found at {CHECK_SCRIPT} — create it first."
    )

    # Build a fixture package.json based on the real one, plus the evil dep
    real_pkg = json.loads(PACKAGE_JSON_PATH.read_text())
    fixture_pkg = dict(real_pkg)
    fixture_pkg["dependencies"] = dict(real_pkg.get("dependencies", {}))
    fixture_pkg["dependencies"]["some-evil-package"] = "1.0.0"

    # tmp/ is gitignored, so a fresh checkout (e.g. CI) won't have it yet.
    (REPO_ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, dir=REPO_ROOT / "tmp"
    ) as f:
        json.dump(fixture_pkg, f)
        tmp_path = Path(f.name)

    try:
        result = subprocess.run(
            ["python3", str(CHECK_SCRIPT), str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode != 0, (
            f"check_node_deps.py exited 0 for a package.json with an un-allowlisted dep. "
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "some-evil-package" in combined, (
            f"Expected 'some-evil-package' in output; got:\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    finally:
        tmp_path.unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════════
# Exit non-zero when allowlist is missing
# ══════════════════════════════════════════════════════════════════════════


def test_check_node_deps_missing_allowlist() -> None:
    """check_node_deps.py must exit non-zero with a clear error when allowlist is missing.

    Uses an invocation that passes a non-existent allowlist path as the second CLI arg.
    RED reason: script does not exist yet → assert script exists first, FAILS.
    """
    # Prerequisite guard — makes the RED reason explicit, not a subprocess side-effect.
    assert CHECK_SCRIPT.exists(), (
        f"scripts/check_node_deps.py not found at {CHECK_SCRIPT} — create it first."
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_allowlist = Path(tmpdir) / "does-not-exist.allowlist"
        # Script CLI: python3 check_node_deps.py [package_json_path [allowlist_path]]
        result = subprocess.run(
            [
                "python3", str(CHECK_SCRIPT),
                str(PACKAGE_JSON_PATH),
                str(fake_allowlist),
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

    assert result.returncode != 0, (
        f"check_node_deps.py should exit non-zero when allowlist is missing; "
        f"got exit {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    # Must not be a raw Python traceback (human-readable error required)
    assert "Traceback" not in combined or "Error:" in combined or "error" in combined.lower(), (
        "Expected a human-readable error message, not a raw Python traceback.\n"
        f"Output: {combined}"
    )
    # Must reference the missing allowlist clearly
    assert any(
        keyword in combined.lower()
        for keyword in ["allowlist", "not found", "missing", "no such file", "error"]
    ), (
        f"Expected allowlist-missing error message in output; got:\n{combined}"
    )


# ══════════════════════════════════════════════════════════════════════════
# Makefile: ci target includes allowlist-node
# ══════════════════════════════════════════════════════════════════════════


def test_makefile_ci_includes_allowlist_node() -> None:
    """The root Makefile ci target must include allowlist-node.

    RED reason: allowlist-node not yet present in the ci target recipe.
    """
    makefile_content = MAKEFILE_PATH.read_text()
    assert "allowlist-node" in makefile_content, (
        "Root Makefile ci target does not include 'allowlist-node'. "
        "Add it during build phase."
    )


def test_makefile_allowlist_node_target_invokes_script() -> None:
    """The root Makefile must have an allowlist-node target calling check_node_deps.py.

    RED reason: target does not exist yet.
    """
    makefile_content = MAKEFILE_PATH.read_text()
    assert "allowlist-node" in makefile_content, (
        "Root Makefile has no 'allowlist-node' target. Add it during build phase."
    )
    assert "check_node_deps.py" in makefile_content, (
        "Root Makefile allowlist-node target does not invoke check_node_deps.py."
    )
