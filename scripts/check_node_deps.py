#!/usr/bin/env python3
"""check_node_deps.py — node-dependency allowlist gate.

Usage:
    python3 scripts/check_node_deps.py [package_json_path [allowlist_path]]

Defaults:
    package_json_path : apps/dashboard/package.json
    allowlist_path    : .add/node-dependencies.allowlist

Exit codes:
    0 — all packages in allowlist
    1 — one or more packages not in allowlist, OR allowlist file not found
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _load_allowlist(allowlist_path: Path) -> set[str]:
    """Load allowlist lines, stripping comments and blank lines."""
    if not allowlist_path.exists():
        print(
            f"check_node_deps: ERROR — allowlist file not found: {allowlist_path}",
            file=sys.stderr,
        )
        sys.exit(1)
    lines: set[str] = set()
    for line in allowlist_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.add(stripped)
    return lines


def _load_packages(package_json_path: Path) -> set[str]:
    """Collect all keys from dependencies + devDependencies in package.json."""
    if not package_json_path.exists():
        print(
            f"check_node_deps: ERROR — package.json not found: {package_json_path}",
            file=sys.stderr,
        )
        sys.exit(1)
    data = json.loads(package_json_path.read_text(encoding="utf-8"))
    packages: set[str] = set()
    packages.update(data.get("dependencies", {}).keys())
    packages.update(data.get("devDependencies", {}).keys())
    return packages


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent

    # CLI: optional package.json path, optional allowlist path
    package_json_path = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else repo_root / "apps" / "dashboard" / "package.json"
    )
    allowlist_path = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else repo_root / ".add" / "node-dependencies.allowlist"
    )

    allowlist = _load_allowlist(allowlist_path)
    packages = _load_packages(package_json_path)

    offenders = sorted(packages - allowlist)
    if offenders:
        print(
            "check_node_deps: FAIL — packages not in node-dependencies.allowlist:",
            file=sys.stderr,
        )
        for pkg in offenders:
            print(f"  {pkg}", file=sys.stderr)
        sys.exit(1)

    print(f"check_node_deps: OK — {len(packages)} packages clean")


if __name__ == "__main__":
    main()
