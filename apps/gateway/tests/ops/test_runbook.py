"""Failing-first (RED) suite for backup-rollback runbook presence.

Scenarios covered (ops-hardening TASK.md §2):
  - Scenario: backup-rollback runbook file exists with all required sections

RED reason expected:
  docs/runbooks/backup-rollback.md does not exist yet.
  Tests fail with AssertionError (file not found) — RIGHT reason.
"""

from __future__ import annotations

from pathlib import Path

# ── Repo root ──────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
RUNBOOK_PATH = REPO_ROOT / "docs" / "runbooks" / "backup-rollback.md"

# Required H2 section headings (exact strings; contract §3)
REQUIRED_SECTIONS = [
    "## Scheduled pg_dump backup",
    "## Restore drill",
    "## Alembic downgrade rollback",
    "## Gateway image rollback",
    "## Secrets handling",
]


def test_runbook_file_exists() -> None:
    """docs/runbooks/backup-rollback.md must exist.

    RED reason: file has not been created yet.
    """
    assert RUNBOOK_PATH.exists(), (
        f"Runbook not found at {RUNBOOK_PATH}. "
        "Create docs/runbooks/backup-rollback.md during build phase."
    )
    assert RUNBOOK_PATH.is_file(), f"{RUNBOOK_PATH} is not a file"


def test_runbook_has_all_required_sections() -> None:
    """The runbook must contain all five required H2 section headings.

    RED reason: file does not exist yet (also fails test_runbook_file_exists).
    Once the file exists, this test enforces section completeness.
    """
    if not RUNBOOK_PATH.exists():
        pytest.fail(  # type: ignore[attr-defined]
            f"Cannot check sections — runbook missing at {RUNBOOK_PATH}"
        )

    content = RUNBOOK_PATH.read_text(encoding="utf-8")
    missing = [s for s in REQUIRED_SECTIONS if s not in content]
    assert not missing, (
        f"Runbook at {RUNBOOK_PATH} is missing required sections:\n"
        + "\n".join(f"  {s!r}" for s in missing)
    )


# Import pytest for the pytest.fail call above
import pytest  # noqa: E402
