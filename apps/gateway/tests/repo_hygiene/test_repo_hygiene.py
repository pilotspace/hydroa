"""Repo-hygiene gates — the checks that keep `make ci` honest.

Two kinds of test live here, and the difference matters.

*Exit-code gates* (`ruff`, `pyright`) shell out to the real tool rather than
re-implementing its judgment, which would drift the moment a rule is added.
They are red until the debt is cleared and green forever after.

*Standing guards* are the ones that still matter in six months. They fail on
the two dishonest ways this debt gets "cleared":

* loosening the linter instead of fixing the code — `select`/`ignore`, the
  pyright `report*` settings and `exclude` are pinned, and every
  per-file-ignores entry must carry a written justification;
* deleting an inconvenient test payload — the Cyrillic homoglyph in the
  Bedrock residency test is the *subject* of that test, and removing it would
  gut a security regression while leaving the suite green.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = GATEWAY_ROOT.parents[1]
PYPROJECT = GATEWAY_ROOT / "pyproject.toml"
ALLOWLIST = REPO_ROOT / ".add" / "dependencies.allowlist"
BEDROCK_GUARD_TEST = (
    GATEWAY_ROOT
    / "tests"
    / "bedrock_region_guard"
    / "test_bedrock_guard_verify2_dispatch_gate.py"
)

# Pinned at freeze. Changing these is a contract change, not a lint fix.
FROZEN_RUFF_SELECT = ["E", "F", "I", "UP", "B", "ASYNC", "S", "RUF"]
FROZEN_RUFF_IGNORE = ["S101"]
FROZEN_PYRIGHT_REPORTS = {
    "reportUnknownVariableType": False,
    "reportUnknownMemberType": False,
    "reportUnknownArgumentType": False,
    "reportUnknownLambdaType": False,
    "reportMissingTypeStubs": False,
}

# Re-pinned by CR v2 (2026-07-25): 49 pre-existing entries + the 63 frozen TEST
# files admitted when `ruff format --check` first ran (it had been short-circuited
# behind a failing `ruff check`). The list may SHRINK as no-edit contracts retire;
# it must never grow again without another contract change.
FROZEN_EXCLUDE_COUNT = 112

# CYRILLIC SMALL LETTER IE — the payload of
# test_unicode_confusable_prefix_never_reaches_bedrock_adapter.
CYRILLIC_IE = "е"  # noqa: RUF001 — this IS the character under guard, not a typo


def _pyproject() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _tool_ruff() -> dict[str, object]:
    tool = _pyproject()["tool"]
    assert isinstance(tool, dict)
    ruff = tool["ruff"]
    assert isinstance(ruff, dict)
    return ruff


def test_ruff_is_clean() -> None:
    """`make lint`'s ruff half must exit 0."""
    result = subprocess.run(
        # noqa: S607 — `uv` is resolved from the developer/CI PATH on purpose; this
        # is a repo-hygiene harness invoking the project's own dev toolchain, not a
        # service shelling out to an attacker-influenced binary.
        ["uv", "run", "ruff", "check", ".", "--output-format=concise"],  # noqa: S607
        cwd=GATEWAY_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"ruff findings remain:\n{result.stdout}"


def test_pyright_is_clean() -> None:
    """`make typecheck` must exit 0."""
    result = subprocess.run(
        ["uv", "run", "pyright"],  # noqa: S607 — same as above: project dev toolchain
        cwd=GATEWAY_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"pyright findings remain:\n{result.stdout}"


def test_allowlist_gate_passes() -> None:
    """Every dependency must be declared in the supply-chain allow-list.

    The gate this asserts had been red since #89 — four dependencies entered the
    tree without the justified entry `.add/dependencies.allowlist` demands.
    """
    result = subprocess.run(
        # noqa: S607 — `python3` from PATH; the script path is a repo-relative literal
        ["python3", "scripts/check_allowlist.py"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"allowlist gate failing:\n{result.stdout}"


def test_new_allowlist_entries_carry_a_justification() -> None:
    """The four dependencies added by this sweep must say why they are here.

    An allow-list entry with no reason records that someone typed a name, not
    that anyone made a decision — which is the whole control.
    """
    added = {"dnspython", "pgvector", "pytest-rerunfailures", "pytest-xdist"}
    unjustified: list[str] = []
    seen: set[str] = set()
    for raw in ALLOWLIST.read_text().splitlines():
        name = raw.split("#", 1)[0].strip()
        if name not in added:
            continue
        seen.add(name)
        if "#" not in raw or not raw.split("#", 1)[1].strip():
            unjustified.append(name)
    missing = added - seen
    assert not missing, f"not allowlisted at all: {sorted(missing)}"
    assert not unjustified, (
        f"allowlisted with no written justification: {sorted(unjustified)} — "
        "follow the python3-saml / reportlab convention"
    )


def test_linter_config_was_not_weakened() -> None:
    """A green bought by loosening the linter is not a green.

    Deliberately does NOT forbid new per-file-ignores entries — the repo's own
    convention uses them for proven false positives (two justified `S608`
    entries predate this task). It forces each to be justified in writing,
    which is the property that actually matters.
    """
    ruff = _tool_ruff()
    lint = ruff.get("lint")
    assert isinstance(lint, dict)

    assert lint.get("select") == FROZEN_RUFF_SELECT, (
        "ruff `select` changed — removing a rule is not a lint fix"
    )
    assert lint.get("ignore") == FROZEN_RUFF_IGNORE, (
        "ruff `ignore` changed — silencing a rule globally is not a lint fix"
    )

    tool = _pyproject()["tool"]
    assert isinstance(tool, dict)
    pyright = tool["pyright"]
    assert isinstance(pyright, dict)
    for key, expected in FROZEN_PYRIGHT_REPORTS.items():
        assert pyright.get(key) == expected, f"pyright setting {key} was relaxed"
    relaxed = [
        key
        for key in pyright
        if key.startswith("report")
        and key not in FROZEN_PYRIGHT_REPORTS
        and pyright[key] is False
    ]
    assert not relaxed, f"new pyright checks disabled: {relaxed}"

    per_file = lint.get("per-file-ignores")
    assert isinstance(per_file, dict)
    source = PYPROJECT.read_text()
    for pattern in per_file:
        line = next(
            (ln for ln in source.splitlines() if ln.lstrip().startswith(f'"{pattern}"')),
            None,
        )
        assert line is not None, f"could not locate per-file-ignores line for {pattern}"
        assert "#" in line and line.split("#", 1)[1].strip(), (
            f"per-file-ignores entry {pattern!r} carries no justification"
        )

    # Match the EXECUTABLE directive — one at the start of a line — not a
    # mention of it in prose. (This module discusses the directive by name; a
    # naive substring search flags itself, which is a test bug, not a finding.)
    # A directive carrying a written justification is allowed, on the same
    # reasoning as per-file-ignores; a BARE blanket suppression is not.
    unjustified_blanket: list[str] = []
    for path in GATEWAY_ROOT.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        for line in path.read_text().splitlines():
            if not line.startswith("# ruff: noqa"):
                continue
            rest = line[len("# ruff: noqa") :].lstrip(": ")
            codes, _, reason = rest.partition(" ")
            if not codes or not reason.strip():
                unjustified_blanket.append(str(path.relative_to(GATEWAY_ROOT)))
    assert not unjustified_blanket, (
        "bare file-level `# ruff: noqa` (no codes, or no written reason) in "
        f"{unjustified_blanket} — suppress specific codes with a justification"
    )


def test_bedrock_homoglyph_payload_survives() -> None:
    """The Cyrillic `е` in the residency guard test is the TEST PAYLOAD.

    `test_unicode_confusable_prefix_never_reaches_bedrock_adapter` proves an
    EU-residency model id cannot slip past the catalog gate when an attacker
    substitutes U+0435 for Latin `e`. "Cleaning up the ambiguous character"
    would leave the test green while deleting the thing it tests. This guard
    makes that mistake loud.
    """
    source = BEDROCK_GUARD_TEST.read_text()
    assert f"{CYRILLIC_IE}u.anthropic" in source, (
        "the U+0435 homoglyph payload is gone from "
        f"{BEDROCK_GUARD_TEST.name} — the residency confusable test no longer "
        "tests a confusable. Restore it; suppress RUF001 per-line instead."
    )


def test_ruff_exclude_gained_no_new_path() -> None:
    """Excluding a file is not fixing it.

    The existing `exclude` list is frozen-test files with a documented
    no-edit contract; growing it to dodge a finding is `gate_weakened`.
    """
    excluded = _tool_ruff().get("exclude")
    assert isinstance(excluded, list)
    assert len(excluded) <= FROZEN_EXCLUDE_COUNT, (
        f"ruff `exclude` grew ({len(excluded)} > {FROZEN_EXCLUDE_COUNT}) — "
        "a new exclusion dodges a finding instead of fixing it"
    )

    # CR v2 rule (i): only TEST files may be excluded. An excluded `src/` file would
    # mean production code is exempt from formatting — a different thing entirely
    # from honouring another task's frozen no-edit contract.
    non_test = [path for path in excluded if not str(path).startswith("tests/")]
    assert not non_test, (
        f"non-test paths in ruff `exclude`: {non_test} — production code may not be "
        "exempted from formatting"
    )
