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

EGRESS_KWARG = "egress_policy"

# WHY THE SCOPE RULE IS STRUCTURAL AND NOT A NAME CONVENTION.
#
# This guard originally narrowed its population with a name-suffix filter
# (`("Provider", "Dialer", "TokenProvider")`). Measured 2026-08-11: `src/gateway` has SIX
# classes taking an `egress_policy` keyword, and that filter policed TWO of them. The four it
# dropped — AzureADTokenProviderCache, AzureCompletionUpstream, HttpxWebhookSink,
# McpCallUseCase — were not edge cases: three of them default the parameter to
# `DenyPrivateAndMetadataEgressPolicy()` and call `self._egress_policy.check(url)` on a real
# dial, i.e. they are the SAME hazard the guard was written for, and 14 test construction
# sites were inheriting the production policy with nothing saying so.
#
# So the discovery step was honest (it reads every `__init__`) and the filter then threw most
# of it away silently — two declarations of one fact, drifting. The rule is now the actual
# hazard, read off the signature: a seam is in scope when `egress_policy` HAS A DEFAULT, so
# omitting it silently inherits production. `McpCallUseCase` takes it as a REQUIRED keyword
# and therefore cannot exhibit the defect at all; it drops out for a reason that is true,
# rather than for having the wrong noun in its name.

# The declared exemption, same shape as the sibling `# NEGATIVE WAIT:` marker: a provider
# that never reaches a dial never consults its policy, so injecting one would be noise. No
# static check can tell "never dialed" from "dialed in a caller three frames away", so the
# site states which it is.
#
# `construction-only` is the original spelling and stays valid. `not dialed` is the general
# form, added because the widened population includes a site that DOES call a method which
# fails before any URL is built (byok_verify's ProviderKeyMissing path) — calling that
# "construction-only" would be untrue, and a marker people have to lie slightly to use is a
# marker they stop reading.
CONSTRUCTION_ONLY = re.compile(r"#\s*EGRESS: (?:construction-only|not dialed)\s*[—-]\s*\S+")
DECLARATION_WINDOW = 6

# The module-level form, for a suite where EVERY construction is fake-backed. It exists for
# tests/dynamic_auth_byok/test_azure_ad_provider_cache.py, which builds the cache 9 times with
# an injected `provider_factory` (so the real provider that would consult the policy is never
# built) and is a FROZEN contract file — nine pasted per-call comments would be both ceremony
# and nine edits to a frozen file.
#
# Deliberately coarse: it exempts every construction in the module, including ones added
# later. Prefer the per-call form; this is for the fake-factory-throughout case where the
# module docstring already states the property.
MODULE_NOT_DIALED = re.compile(r"#\s*EGRESS: module not dialed\s*[—-]\s*\S+")


def _egress_seam_defaults() -> dict[str, bool]:
    """Map each class in src/ whose __init__ takes `egress_policy` to "it has a default".

    True means the parameter can be OMITTED, so the caller silently inherits the production
    deny-by-default policy — the hazard. False means it is required, so the caller must
    choose and the defect is unreachable.
    """
    src = GATEWAY_ROOT / "src"
    found: dict[str, bool] = {}
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
                # Positional params pair with `defaults` right-aligned; keyword-only params
                # pair with `kw_defaults` index-for-index, where None means "no default".
                positional = item.args.args
                pos_defaults: list[ast.expr | None] = [None] * (
                    len(positional) - len(item.args.defaults)
                ) + list(item.args.defaults)
                pairs: list[tuple[str, ast.expr | None]] = [
                    (a.arg, d) for a, d in zip(positional, pos_defaults, strict=True)
                ]
                pairs += [
                    (a.arg, d)
                    for a, d in zip(item.args.kwonlyargs, item.args.kw_defaults, strict=True)
                ]
                for name, default in pairs:
                    if name == EGRESS_KWARG:
                        found[node.name] = default is not None
    return found


def _providers_with_an_egress_seam() -> set[str]:
    """The IN-SCOPE seams: those whose `egress_policy` can be omitted."""
    return {name for name, has_default in _egress_seam_defaults().items() if has_default}


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
        if MODULE_NOT_DIALED.search(source):
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


def test_the_guard_polices_every_omittable_egress_seam() -> None:
    """The scope rule must stay derived from src, not from a name convention.

    This is the drift check that the name-suffix filter needed and did not have. It asserts
    the population is non-empty (so a renamed seam cannot make the whole guard vacuous) and
    that the only classes left unpoliced are the ones where `egress_policy` is REQUIRED, which
    is a property of the signature rather than an opinion about the class name.
    """
    seams = _egress_seam_defaults()
    assert seams, (
        f"no class in src/ takes an {EGRESS_KWARG}= parameter — the seam this guard keys off "
        "has changed shape, so the guard above is now covering nothing"
    )

    policed = _providers_with_an_egress_seam()
    assert policed, (
        f"every {EGRESS_KWARG} parameter in src/ is now REQUIRED. That would be good news — "
        "the defect class would be structurally unreachable — but this guard would then be "
        "covering nothing, so delete it deliberately rather than leaving it green and inert."
    )

    unpoliced = {name for name, has_default in seams.items() if not has_default}
    overlap = policed & unpoliced
    assert not overlap, f"a class cannot be both policed and unpoliced: {sorted(overlap)}"
    assert policed | unpoliced == set(seams), (
        "every discovered seam must be classified as policed or unpoliced; these fell "
        f"through: {sorted(set(seams) - policed - unpoliced)}"
    )
