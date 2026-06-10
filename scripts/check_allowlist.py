#!/usr/bin/env python3
"""ADD pipeline gate: reject dependencies not present in dependencies.allowlist.

Scans every apps/*/pyproject.toml [project.dependencies] and
[dependency-groups] entries, normalizes package names (PEP 503), and fails
with a non-zero exit if any package is not in the allowlist.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "dependencies.allowlist"


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    # "fastapi[standard]>=0.115; python_version >= '3.12'" -> "fastapi"
    match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    if not match:
        raise ValueError(f"Unparseable requirement: {requirement!r}")
    return normalize(match.group(1))


def load_allowlist() -> set[str]:
    allowed: set[str] = set()
    for line in ALLOWLIST.read_text().splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            allowed.add(normalize(entry))
    return allowed


def collect_dependencies(pyproject: Path) -> set[str]:
    data = tomllib.loads(pyproject.read_text())
    requirements: list[str] = list(data.get("project", {}).get("dependencies", []))
    for group in data.get("dependency-groups", {}).values():
        requirements.extend(r for r in group if isinstance(r, str))
    for extra in data.get("project", {}).get("optional-dependencies", {}).values():
        requirements.extend(extra)
    return {requirement_name(r) for r in requirements}


def main() -> int:
    allowed = load_allowlist()
    violations: list[tuple[Path, str]] = []
    pyprojects = sorted(ROOT.glob("apps/*/pyproject.toml"))
    if not pyprojects:
        print("check_allowlist: no apps/*/pyproject.toml found — nothing to check")
        return 0
    for pyproject in pyprojects:
        for name in sorted(collect_dependencies(pyproject)):
            if name not in allowed:
                violations.append((pyproject.relative_to(ROOT), name))
    if violations:
        print("check_allowlist: FAIL — packages not in dependencies.allowlist:")
        for path, name in violations:
            print(f"  {path}: {name}")
        print("Add the package to dependencies.allowlist (own PR, justified) or remove it.")
        return 1
    print(f"check_allowlist: OK — {len(pyprojects)} manifest(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
