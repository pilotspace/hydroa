"""Failing-first (RED) suite for rename-hydroa (contract DRAFT, TASK.md §4).

Six tests: R1–R5 must be RED before build (wrong values in source); R6 must be GREEN
(compat pins verified present — these must NEVER change).

TRUE-RED rule: each test asserts the TARGET post-rename state and fails NOW because the
rename has not been applied yet. The failure must be for the RIGHT reason (wrong value or
wrong literal present), not a test bug (import error, fixture crash, etc.).

Right-reason red targets:

  R1  (pyproject name): tomllib.load → [project][name] == "gateway" ≠ "hydroa-gateway"
      AssertionError: expected "hydroa-gateway", got "gateway"

  R2  (app title): create_app(settings).title == "AI Proxy Gateway" ≠ "Hydroa Gateway"
      AssertionError: expected "Hydroa Gateway", got "AI Proxy Gateway"

  R3  (compose names): each file's top-level name starts with "ai-proxy-" not "hydroa-"
      AssertionError: name "ai-proxy-dev" does not start with "hydroa-"  (etc.)

  R4  (live-script container refs): each script still contains the literal "ai-proxy-"
      AssertionError: "ai-proxy-" still present in scripts/live_smoke.py  (etc.)

  R5  (live_v4_verify OIDC identity): the fixed-prefix literal f"user@{ still appears
      in scripts/live_v4_verify.py (line ~572: oidc_email = f"user@{OIDC_DOMAIN}")
      AssertionError: fixed OIDC identity literal still present; no run-id interpolation found

  R6  (compat pins — GREEN BY DESIGN): all five compat-pin literals are present as they
      must always be. This test is the regression guard: if a future commit accidentally
      removes or renames a wire-visible identifier, this test turns red and blocks CI.

Infrastructure: no DB, no Redis, no network — pure file reads and a single create_app call.
Runs inside `make ci` without any live stack.

Suite-local conftest: apps/gateway/tests/rename_hydroa/conftest.py (per CONVENTIONS.md
autouse-fixtures rule; no root-level conftest added).
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Repo-root resolution (suite runs from apps/gateway/ via pytest testpaths)
# ---------------------------------------------------------------------------

# The suite lives at apps/gateway/tests/rename_hydroa/test_rename_hydroa.py.
# All path targets are expressed relative to the repo root.
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[4]  # …/ai-proxy

_PYPROJECT = _REPO_ROOT / "apps" / "gateway" / "pyproject.toml"
_COMPOSE_DEV = _REPO_ROOT / "infra" / "docker-compose.dev.yml"
_COMPOSE_E2E = _REPO_ROOT / "infra" / "docker-compose.e2e.yml"
_COMPOSE_PROD = _REPO_ROOT / "infra" / "docker-compose.prod.yml"
_SCRIPT_SMOKE = _REPO_ROOT / "scripts" / "live_smoke.py"
_SCRIPT_V3 = _REPO_ROOT / "scripts" / "live_v3_verify.py"
_SCRIPT_V4 = _REPO_ROOT / "scripts" / "live_v4_verify.py"
_OIDC_ROUTER = _REPO_ROOT / "apps" / "gateway" / "src" / "gateway" / "auth" / "api" / "oidc_router.py"
_OTEL_PY = _REPO_ROOT / "apps" / "gateway" / "src" / "gateway" / "observability" / "otel.py"
_CONFIG_PY = _REPO_ROOT / "apps" / "gateway" / "src" / "gateway" / "core" / "config.py"
_ENVOY_YAML = _REPO_ROOT / "infra" / "envoy" / "envoy.yaml"
_ENVOY_PROD_YAML = _REPO_ROOT / "infra" / "envoy" / "envoy-prod.yaml"


# ---------------------------------------------------------------------------
# Settings helper — minimal, no DB/Redis needed for R2
# ---------------------------------------------------------------------------

def _make_minimal_settings():  # type: ignore[return]
    """Build the smallest valid Settings object for create_app introspection.

    Mirrors the make_base_settings pattern from frozen oidc_tenant_config suite.
    No DB/Redis/OIDC fields required — create_app does not open connections at
    construction time; lifespan is not triggered in this introspection path.
    """
    # Import here so the module-level import of yaml/tomllib never triggers
    # heavy gateway startup work.
    from gateway.core.config import Settings  # noqa: PLC0415

    return Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",  # noqa: S106
        redis_url="redis://localhost:6380/9",
    )


# ---------------------------------------------------------------------------
# R1 · pyproject [project] name == "hydroa-gateway"
# ---------------------------------------------------------------------------


def test_pyproject_name_is_hydroa_gateway() -> None:
    """R1 RED: apps/gateway/pyproject.toml [project] name must be "hydroa-gateway".

    Currently name = "gateway". The build renames it to "hydroa-gateway".

    Right-reason failure: AssertionError — expected "hydroa-gateway", got "gateway".
    NOT an ImportError or FileNotFoundError.
    """
    assert _PYPROJECT.exists(), f"pyproject.toml not found at {_PYPROJECT}"

    with _PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)

    project_name = data["project"]["name"]
    assert project_name == "hydroa-gateway", (
        f"[project] name must be 'hydroa-gateway' after the rename pass; "
        f"got '{project_name}'. "
        f"Fix: edit apps/gateway/pyproject.toml [project] name."
    )

    # Verify import package is unchanged (belt-and-suspenders: hatch wheel config
    # must still point to src/gateway so the `gateway` import stays valid).
    wheel_packages = data.get("tool", {}).get("hatch", {}).get("build", {}).get(
        "targets", {}
    ).get("wheel", {}).get("packages", [])
    assert "src/gateway" in wheel_packages, (
        f"[tool.hatch.build.targets.wheel] packages must still include 'src/gateway' "
        f"(import package must NOT be renamed); got packages={wheel_packages}"
    )


# ---------------------------------------------------------------------------
# R2 · FastAPI app title == "Hydroa Gateway"
# ---------------------------------------------------------------------------


def test_app_title_is_hydroa_gateway() -> None:
    """R2 RED: create_app() must return a FastAPI app with title == "Hydroa Gateway".

    Currently main.py has FastAPI(title="AI Proxy Gateway").
    The build changes it to FastAPI(title="Hydroa Gateway").

    Right-reason failure: AssertionError — expected "Hydroa Gateway", got "AI Proxy Gateway".
    NOT an ImportError (the import must succeed; failure is in the title value).
    """
    # Import must succeed (this is testing the title value, not import existence).
    from gateway.main import create_app  # noqa: PLC0415

    settings = _make_minimal_settings()
    app = create_app(settings)

    assert app.title == "Hydroa Gateway", (
        f"FastAPI app title must be 'Hydroa Gateway' after the rename pass; "
        f"got '{app.title}'. "
        f"Fix: edit apps/gateway/src/gateway/main.py FastAPI(title=...) argument."
    )


# ---------------------------------------------------------------------------
# R3 · compose file top-level name: starts with "hydroa-"
# ---------------------------------------------------------------------------


def test_compose_names_are_hydroa() -> None:
    """R3 RED: each compose file's top-level `name:` must start with "hydroa-".

    Currently:
      docker-compose.dev.yml  → name: ai-proxy-dev
      docker-compose.e2e.yml  → name: ai-proxy-e2e
      docker-compose.prod.yml → name: ai-proxy-prod

    The build renames them to hydroa-dev, hydroa-e2e, hydroa-prod respectively.

    Right-reason failure: AssertionError per file — "ai-proxy-dev" does not start
    with "hydroa-". Three compose files checked; all must pass for this test to pass.
    """
    compose_files = {
        "docker-compose.dev.yml": _COMPOSE_DEV,
        "docker-compose.e2e.yml": _COMPOSE_E2E,
        "docker-compose.prod.yml": _COMPOSE_PROD,
    }

    failures: list[str] = []
    for label, path in compose_files.items():
        assert path.exists(), f"compose file not found: {path}"
        with path.open() as fh:
            data = yaml.safe_load(fh)
        name = data.get("name", "")
        if not name.startswith("hydroa-"):
            failures.append(
                f"{label}: top-level name '{name}' does not start with 'hydroa-' "
                f"(expected 'hydroa-dev', 'hydroa-e2e', or 'hydroa-prod')"
            )

    assert not failures, (
        "Compose project names must all start with 'hydroa-' after the rename pass.\n"
        + "\n".join(failures)
        + "\nFix: update the `name:` field at the top of each compose file."
    )


# ---------------------------------------------------------------------------
# R4 · live scripts contain no "ai-proxy-" container name literals
# ---------------------------------------------------------------------------


def test_live_scripts_have_no_ai_proxy_container_refs() -> None:
    """R4 RED: no live script may contain the literal "ai-proxy-" after the rename.

    Currently:
      live_smoke.py     → "ai-proxy-e2e-gateway-1" (line ~72), "ai-proxy-e2e-postgres-1" (line ~142)
      live_v3_verify.py → PG_CONTAINER = "ai-proxy-e2e-postgres-1" (line ~45)
      live_v4_verify.py → PG_CONTAINER = "ai-proxy-e2e-postgres-1" (line ~52),
                          GW_CONTAINER = "ai-proxy-e2e-gateway-1" (line ~53)

    The build replaces all "ai-proxy-" container name literals with their
    "hydroa-e2e-" equivalents in the same commit as the compose name changes.

    Right-reason failure: AssertionError per script — the literal "ai-proxy-" still
    appears in the file text. Must fail for ALL three scripts before the build.
    """
    scripts = {
        "live_smoke.py": _SCRIPT_SMOKE,
        "live_v3_verify.py": _SCRIPT_V3,
        "live_v4_verify.py": _SCRIPT_V4,
    }

    failures: list[str] = []
    for label, path in scripts.items():
        assert path.exists(), f"live script not found: {path}"
        text = path.read_text(encoding="utf-8")
        occurrences = [
            f"  line {i + 1}: {line.rstrip()}"
            for i, line in enumerate(text.splitlines())
            if "ai-proxy-" in line
        ]
        if occurrences:
            failures.append(
                f"{label}: 'ai-proxy-' still present in {len(occurrences)} line(s):\n"
                + "\n".join(occurrences)
            )

    assert not failures, (
        "Live scripts must not reference 'ai-proxy-' container names after the rename pass.\n"
        "All 'ai-proxy-e2e-gateway-1' → 'hydroa-e2e-gateway-1' and "
        "'ai-proxy-e2e-postgres-1' → 'hydroa-e2e-postgres-1'.\n\n"
        + "\n\n".join(failures)
    )


# ---------------------------------------------------------------------------
# R5 · live_v4_verify.py has no fixed OIDC identity
# ---------------------------------------------------------------------------

# The pattern we check for (the fixed-prefix form that must be ABSENT after the build).
# This is a mechanical string pin: if `f"user@{` appears verbatim in the file, the OIDC
# identity is still fixed per-run (e.g. `f"user@{OIDC_DOMAIN}"` = same address every run).
# After the build it must become `f"user-{run_id}@{...}"` or similar per-run-unique form.
_FIXED_OIDC_PATTERN = re.compile(r'f"user@\{')


def test_live_v4_verify_oidc_identity_is_per_run_unique() -> None:
    """R5 RED: live_v4_verify.py must not contain a fixed OIDC identity (f"user@{...}").

    Currently (line ~572):
        oidc_email = f"user@{OIDC_DOMAIN}"

    This produces the same email address on every invocation. Against a long-lived
    stack the OIDC user row already exists from the previous run, causing a collision.

    The build must introduce a per-run run_id variable (e.g. int(time.time()) or
    uuid4 hex) and rewrite this line to f"user-{run_id}@{OIDC_DOMAIN}" or similar.

    Right-reason failure (before build):
      AssertionError: fixed OIDC identity literal f"user@{ is still present.

    After build, this test verifies TWO things:
      a) The fixed-prefix form is absent.
      b) A run-id interpolation is present (run_id variable or equivalent unique prefix).

    The run-id presence check uses a broad heuristic regex — it should match any of:
      f"user-{run_id}@    f"user-{RUN_ID}@    f"user-{ts}@    etc.
    """
    assert _SCRIPT_V4.exists(), f"live_v4_verify.py not found at {_SCRIPT_V4}"
    text = _SCRIPT_V4.read_text(encoding="utf-8")

    # Primary assertion: fixed-prefix form must be absent.
    fixed_lines = [
        f"  line {i + 1}: {line.rstrip()}"
        for i, line in enumerate(text.splitlines())
        if _FIXED_OIDC_PATTERN.search(line)
    ]
    assert not fixed_lines, (
        "live_v4_verify.py still contains a fixed OIDC identity (f\"user@{...}\").\n"
        "These lines must be replaced with a per-run-unique form "
        "(e.g. f\"user-{run_id}@{OIDC_DOMAIN}\"):\n"
        + "\n".join(fixed_lines)
        + "\n\nFix: introduce run_id = int(time.time()) at script top and rewrite "
        "oidc_email to f\"user-{run_id}@{OIDC_DOMAIN}\"."
    )

    # Secondary assertion: a run-id interpolation must be present in the oidc_email
    # assignment (broad heuristic — any "user-{" prefix in an f-string on an
    # oidc_email assignment line counts).
    run_id_pattern = re.compile(r'oidc_email\s*=\s*f["\']user-\{')
    has_run_id = bool(run_id_pattern.search(text))
    assert has_run_id, (
        "live_v4_verify.py must assign oidc_email using a per-run-unique prefix "
        "(e.g. oidc_email = f\"user-{run_id}@{OIDC_DOMAIN}\"). "
        "No such assignment was found after removing the fixed-prefix form."
    )


# ---------------------------------------------------------------------------
# R6 · compat pins preserved — GREEN BY DESIGN (regression guard)
# ---------------------------------------------------------------------------


def test_compat_pins_preserved() -> None:
    """R6 GREEN BY DESIGN: wire-visible identifiers must never change.

    Rationale (per §3 CONTRACT compat-pin list and MILESTONE.md Out clause):
      - Cookie name "ai_proxy_session": changing it invalidates live sessions and
        breaks every frozen test that asserts the cookie name.
      - OTel span attribute "ai_proxy.tenant_id": external trace pipelines and
        dashboards key on ai_proxy.* attrs; frozen obs_callbacks suite pins them.
      - JWT issuer "ai-proxy": changing the default invalidates live JWT tokens and
        breaks Envoy JWT filter validation (both envoy yamls use this issuer).
      - Envoy issuer: "ai-proxy": the JWT filter validates tokens against this string;
        must match the gateway jwt_issuer default.

    This test is intentionally GREEN before the build and must REMAIN green after it.
    If this test ever turns red, a wire-breaking change has been introduced — HARD-STOP.

    Five assertions, five files, all compat-pinned literals.
    """
    # (a) Cookie name compat pin
    assert _OIDC_ROUTER.exists(), f"oidc_router.py not found at {_OIDC_ROUTER}"
    oidc_router_text = _OIDC_ROUTER.read_text(encoding="utf-8")
    assert "ai_proxy_session" in oidc_router_text, (
        "COMPAT PIN VIOLATION: 'ai_proxy_session' cookie name was removed or renamed "
        "in gateway/auth/api/oidc_router.py. This is a HARD-STOP — changing the cookie "
        "name breaks live sessions and frozen sso-oidc/oidc-jwks/oidc-tenant-config suites."
    )

    # (b) OTel span attribute compat pin
    assert _OTEL_PY.exists(), f"otel.py not found at {_OTEL_PY}"
    otel_text = _OTEL_PY.read_text(encoding="utf-8")
    assert "ai_proxy.tenant_id" in otel_text, (
        "COMPAT PIN VIOLATION: 'ai_proxy.tenant_id' span attribute was removed or renamed "
        "in gateway/observability/otel.py. This is a HARD-STOP — ai_proxy.* span attrs "
        "are wire-visible (external trace pipelines) and frozen obs_callbacks suite pins them."
    )

    # (c) JWT issuer default compat pin
    assert _CONFIG_PY.exists(), f"config.py not found at {_CONFIG_PY}"
    config_text = _CONFIG_PY.read_text(encoding="utf-8")
    assert '"ai-proxy"' in config_text, (
        "COMPAT PIN VIOLATION: jwt_issuer default 'ai-proxy' was removed or renamed "
        "in gateway/core/config.py. This is a HARD-STOP — the issuer must match the "
        "Envoy JWT filter config and live token claims."
    )
    # Tighter check: the default assignment line specifically
    assert re.search(r'jwt_issuer\s*:\s*str\s*=\s*"ai-proxy"', config_text), (
        "COMPAT PIN VIOLATION: jwt_issuer field default must be exactly \"ai-proxy\" "
        "in gateway/core/config.py (the assignment line was not found in expected form)."
    )

    # (d) Envoy issuer compat pin — envoy.yaml
    assert _ENVOY_YAML.exists(), f"envoy.yaml not found at {_ENVOY_YAML}"
    envoy_text = _ENVOY_YAML.read_text(encoding="utf-8")
    assert 'issuer: "ai-proxy"' in envoy_text, (
        "COMPAT PIN VIOLATION: issuer: \"ai-proxy\" was removed or changed in "
        "infra/envoy/envoy.yaml. This is a HARD-STOP — the Envoy JWT filter uses this "
        "value to validate token iss claims; changing it breaks JWT validation at the edge."
    )

    # (e) Envoy issuer compat pin — envoy-prod.yaml
    assert _ENVOY_PROD_YAML.exists(), f"envoy-prod.yaml not found at {_ENVOY_PROD_YAML}"
    envoy_prod_text = _ENVOY_PROD_YAML.read_text(encoding="utf-8")
    assert 'issuer: "ai-proxy"' in envoy_prod_text, (
        "COMPAT PIN VIOLATION: issuer: \"ai-proxy\" was removed or changed in "
        "infra/envoy/envoy-prod.yaml. This is a HARD-STOP — same reason as envoy.yaml."
    )
