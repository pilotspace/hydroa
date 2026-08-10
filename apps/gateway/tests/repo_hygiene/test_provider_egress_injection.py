"""Standing guard: a test-built provider must inject an egress policy (CR v3, class 5).

Every provider adapter defaults `egress_policy` to the production
`DenyPrivateAndMetadataEgressPolicy`, which RESOLVES THE URL HOST before the request is
issued. In production that is the point. In a test it means a live DNS query runs before
`httpx.MockTransport` is ever reached — so a suite whose own docstring says "All calls go
through httpx.MockTransport — no network required" quietly depends on a resolver.

`tests/azure_audio` did exactly that for months and was green the whole time, because
`myresource.openai.azure.com` resolves through Azure's wildcard DNS to a public address,
which the policy allows. Under 12-way xdist load the lookup can time out, the policy
correctly fails CLOSED, and a test that never intended to exercise egress fails with
ERR_UPSTREAM_EGRESS_DENIED.

The tell that this is a propagation failure rather than a one-off: every sibling suite —
azure_chat, azure_embeddings, azure_streaming, azure_aad — already passes
`egress_policy=AllowAllEgressPolicy()`. azure_audio copied the adapter factory without it.
Same shape as the DDL-after-lifespan class: one suite fixed it, the fix did not travel.

A suite that genuinely tests egress behaviour injects a DENYING policy (or asserts on the
error), so it satisfies this guard too — the requirement is that the policy is CHOSEN, not
inherited by accident.

Covers CR v3 class 5.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = GATEWAY_ROOT / "tests"

# Adapters whose constructor takes `egress_policy` and defaults it to the production policy.
# Derived from the constructor signature below rather than trusted as a literal list.
PROVIDER_SUFFIXES = ("Provider", "Dialer", "TokenProvider")

EGRESS_KWARG = "egress_policy"

# The declared exemption, same shape as the sibling `# NEGATIVE WAIT:` marker: a provider
# that is CONSTRUCTED but never dialed never consults its policy, so injecting one would be
# noise. Two real cases exist — asserting on `_token_url()` and on
# `_client.follow_redirects` — and no static check can tell "never dialed" from "dialed in a
# caller three frames away", so the site states which it is.
CONSTRUCTION_ONLY = re.compile(r"#\s*EGRESS: construction-only\s*[—-]\s*\S+")
DECLARATION_WINDOW = 6


def _providers_with_an_egress_seam() -> set[str]:
    """Class names in src/ whose __init__ accepts an `egress_policy` keyword."""
    src = GATEWAY_ROOT / "src"
    found: set[str] = set()
    for path in src.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if item.name != "__init__":
                    continue
                args = [a.arg for a in item.args.args + item.args.kwonlyargs]
                if EGRESS_KWARG in args:
                    found.add(node.name)
    return found


def _iter_test_modules() -> list[Path]:
    return sorted(p for p in TESTS_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _violations() -> list[str]:
    seams = _providers_with_an_egress_seam()
    if not seams:  # pragma: no cover — the seam was renamed
        return [
            f"no class in src/ takes an {EGRESS_KWARG}= keyword — the seam this guard keys "
            "off has changed, so it is now covering nothing"
        ]

    found: list[str] = []
    for path in _iter_test_modules():
        source = path.read_text()
        if not any(name in source for name in seams):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue
        rel = path.relative_to(GATEWAY_ROOT)
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name is None or name not in seams:
                continue
            if not name.endswith(PROVIDER_SUFFIXES):
                continue
            if any(kw.arg == EGRESS_KWARG for kw in node.keywords):
                continue
            window = lines[max(0, node.lineno - 1 - DECLARATION_WINDOW) : node.lineno]
            if any(CONSTRUCTION_ONLY.search(line) for line in window):
                continue
            found.append(
                f"{rel}:{node.lineno} — {name}(...) built without {EGRESS_KWARG}=, so it "
                f"inherits the production deny-by-default policy and performs a LIVE DNS "
                f"lookup before MockTransport is reached"
            )
    return sorted(found)


def test_test_built_provider_injects_an_egress_policy() -> None:
    """CR v3 class 5 — a provider built in tests must CHOOSE its egress policy.

    Inject `AllowAllEgressPolicy()` for a MockTransport suite, or a denying policy for a
    suite that actually tests egress. What is banned is inheriting the production policy by
    omission and thereby making a unit test depend on a resolver.

    A provider that is constructed but never dialed can declare
    `# EGRESS: construction-only — <why>` instead.
    """
    violations = _violations()
    assert not violations, (
        f"{len(violations)} test-built provider(s) inherit the production egress policy:\n  "
        + "\n  ".join(violations)
    )
