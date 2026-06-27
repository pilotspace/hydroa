"""Red/green contract tests for the ai-proxy Helm chart scaffold (v53 task 1).

Harness: shell out to the real `helm` binary, parse rendered YAML with pyyaml,
and assert on the RENDERED objects (behavior), not template internals. One test
per §2 scenario in `.add/tasks/helm-chart-scaffold/TASK.md`, plus strengthenings
added after an adversarial review (overfit/untested gaps F3-F9).

Red-for-the-right-reason: before the chart exists, `helm template` errors on the
missing chart dir and `values.yaml` cannot be loaded — every test fails. After the
build, every test goes green. `helm` is a hard requirement (installed locally per
the freeze decision); the suite never skips on a missing binary — a skip would make
the green unearned.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]  # tests/helm/ -> repo root
CHART = REPO / "charts" / "ai-proxy"
VALUES = CHART / "values.yaml"
VALUES_PROD = CHART / "values-prod.yaml"
RELEASE = "ai-proxy"

HELM = shutil.which("helm")


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    assert HELM, "helm binary required on PATH (brew install helm) — freeze decision"
    return subprocess.run(
        [HELM, *args], cwd=str(REPO), capture_output=True, text=True
    )


def template(*, set_args: dict[str, str] | None = None,
             set_json: dict[str, str] | None = None,
             extra_files: list[pathlib.Path] | None = None
             ) -> subprocess.CompletedProcess[str]:
    args = ["template", RELEASE, str(CHART)]
    for f in extra_files or []:
        args += ["-f", str(f)]
    for k, v in (set_args or {}).items():
        args += ["--set", f"{k}={v}"]
    for k, v in (set_json or {}).items():
        args += ["--set-json", f"{k}={v}"]
    return _helm(*args)


def docs(proc: subprocess.CompletedProcess[str]) -> list[dict]:
    assert proc.returncode == 0, f"helm template failed:\n{proc.stderr}"
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def by_kind(ds: list[dict], kind: str, name_has: str | None = None) -> list[dict]:
    out = [d for d in ds if d.get("kind") == kind]
    if name_has is not None:
        out = [d for d in out if name_has in d.get("metadata", {}).get("name", "")]
    return out


def gateway_deployment(ds: list[dict]) -> dict:
    deps = by_kind(ds, "Deployment", "gateway")
    assert deps, "no gateway Deployment rendered"
    return deps[0]


def main_container(dep: dict) -> dict:
    return dep["spec"]["template"]["spec"]["containers"][0]


def env_map(container: dict) -> dict[str, dict]:
    return {e["name"]: e for e in container.get("env", [])}


# ── M1 ────────────────────────────────────────────────────────────────────────
def test_lint_and_render_defaults():
    lint = _helm("lint", str(CHART))
    assert lint.returncode == 0, lint.stdout + lint.stderr
    ds = docs(template())
    assert by_kind(ds, "Deployment", "gateway"), "expected a gateway Deployment"
    assert by_kind(ds, "Service", "gateway"), "expected a gateway Service"


# ── M2 / R4 hardcoded_env_value ────────────────────────────────────────────────
def test_gateway_inputs_from_values():
    sentinel_img, sentinel_tag = "sentinel-repo/gw", "sentinel-tag-9z9z"
    sentinel_db = "postgresql+asyncpg://sentinel-host/db"
    ds = docs(template(set_args={
        "image.repository": sentinel_img,
        "image.tag": sentinel_tag,
        "gateway.env.databaseUrl": sentinel_db,
    }))
    c = main_container(gateway_deployment(ds))
    assert c["image"] == f"{sentinel_img}:{sentinel_tag}", c["image"]
    assert env_map(c)["GATEWAY_DATABASE_URL"].get("value") == sentinel_db

    # The sentinels must NOT appear in a default render — proves values-driven,
    # not hardcoded literals in the templates.
    default = docs(template())
    dc = main_container(gateway_deployment(default))
    assert dc["image"] != f"{sentinel_img}:{sentinel_tag}"
    assert env_map(dc)["GATEWAY_DATABASE_URL"].get("value") != sentinel_db


# ── M3 ──────────────────────────────────────────────────────────────────────────
def test_gateway_pod_matches_image_contract():
    dep = gateway_deployment(docs(template()))
    c = main_container(dep)
    ports = [p["containerPort"] for p in c.get("ports", [])]
    assert 8000 in ports, ports
    pod_sc = dep["spec"]["template"]["spec"].get("securityContext", {})
    c_sc = c.get("securityContext", {})
    run_as_user = c_sc.get("runAsUser", pod_sc.get("runAsUser"))
    run_as_nonroot = c_sc.get("runAsNonRoot", pod_sc.get("runAsNonRoot"))
    assert run_as_user == 1000, run_as_user
    assert run_as_nonroot is True
    assert "command" not in c, "must not override the image's --factory CMD"
    assert env_map(c)["GATEWAY_ENVIRONMENT"].get("value") == "production"


# ── M4 / R3 resilience_incomplete ──────────────────────────────────────────────
def test_design_for_failure_complete():
    drain = 30
    ds = docs(template(set_args={"gateway.env.drainTimeoutSeconds": str(drain)}))
    dep = gateway_deployment(ds)
    c = main_container(dep)
    for probe in ("livenessProbe", "readinessProbe", "startupProbe"):
        assert probe in c, f"missing {probe}"
        http = c[probe].get("httpGet", {})
        assert http.get("path") == "/health", (probe, http)
        # Named port "http" must resolve to 8000 (asserted in the image-contract test).
        assert http.get("port") == "http", (probe, http)
    res = c.get("resources", {})
    for tier in ("requests", "limits"):
        assert res.get(tier, {}).get("cpu"), f"missing resources.{tier}.cpu"
        assert res.get(tier, {}).get("memory"), f"missing resources.{tier}.memory"
    assert by_kind(ds, "PodDisruptionBudget", "gateway"), "expected a PDB"
    grace = dep["spec"]["template"]["spec"].get("terminationGracePeriodSeconds")
    # Grace must strictly exceed the configured drain (so the flusher finishes).
    assert grace is not None and grace > drain, (grace, drain)


# ── M5 / R1 secret_literal_forbidden ────────────────────────────────────────────
def test_secrets_reference_only():
    ds = docs(template())
    c = main_container(gateway_deployment(ds))
    jwt = env_map(c)["GATEWAY_JWT_SECRET"]
    assert "value" not in jwt, "JWT secret must not be a plaintext env value"
    assert jwt.get("valueFrom", {}).get("secretKeyRef"), jwt
    # No populated Secret is rendered by default (createSecret=false) — assert
    # explicitly rather than relying on an empty-loop no-op.
    populated = [s for s in by_kind(ds, "Secret")
                 if s.get("data") or s.get("stringData")]
    assert populated == [], f"no populated Secret should render by default: {populated}"


def test_no_plaintext_credentials_in_default_values():
    # M5: the chart ships NO secret value. The default connection strings must not
    # embed credentials (e.g. user:password@host), and a *SecretRef path must exist.
    v = yaml.safe_load(VALUES.read_text())
    for key in ("databaseUrl", "redisUrl"):
        url = v["gateway"]["env"][key]
        host_part = url.split("//", 1)[-1].split("/", 1)[0]
        assert ":" not in host_part.split("@")[0] or "@" not in host_part, \
            f"default {key} embeds a password: {url!r}"
    assert "databaseUrlSecretRef" in v["gateway"]["env"]
    assert "redisUrlSecretRef" in v["gateway"]["env"]


def test_connection_string_can_come_from_secret():
    # External-ready secret path: setting databaseUrlSecretRef sources the whole DSN
    # from a Secret (valueFrom) instead of a plaintext value.
    ds = docs(template(set_args={
        "gateway.env.databaseUrlSecretRef.name": "db-dsn",
        "gateway.env.databaseUrlSecretRef.key": "url",
    }))
    db = env_map(main_container(gateway_deployment(ds)))["GATEWAY_DATABASE_URL"]
    assert "value" not in db, "should source from Secret, not a plaintext value"
    ref = db.get("valueFrom", {}).get("secretKeyRef", {})
    assert ref.get("name") == "db-dsn" and ref.get("key") == "url", db


# ── M6 ──────────────────────────────────────────────────────────────────────────
def test_values_schema_complete():
    v = yaml.safe_load(VALUES.read_text())
    gw = v["gateway"]
    assert isinstance(gw["env"]["databaseUrl"], str)
    assert isinstance(gw["env"]["redisUrl"], str)
    assert "extraEnv" in gw and "extraSecretEnv" in gw
    for placeholder in ("datastores", "envoy", "dashboard"):
        assert placeholder in v, f"missing typed placeholder block: {placeholder}"


def test_extra_env_escape_hatch_renders():
    # M6: extraEnv / extraSecretEnv reach the long-tail GATEWAY_* knobs. Exercise
    # the template range loops, not just the values file.
    ds = docs(template(set_args={
        "gateway.extraEnv[0].name": "GATEWAY_FOO",
        "gateway.extraEnv[0].value": "bar",
        "gateway.extraSecretEnv[0].name": "GATEWAY_SECRET_KNOB",
        "gateway.extraSecretEnv[0].secretRef.name": "knob-secret",
        "gateway.extraSecretEnv[0].secretRef.key": "k",
    }))
    env = env_map(main_container(gateway_deployment(ds)))
    assert env["GATEWAY_FOO"].get("value") == "bar"
    ref = env["GATEWAY_SECRET_KNOB"].get("valueFrom", {}).get("secretKeyRef", {})
    assert ref.get("name") == "knob-secret" and ref.get("key") == "k"


# ── M7 ──────────────────────────────────────────────────────────────────────────
def test_prod_overlay_swaps_without_template_edit():
    base = main_container(gateway_deployment(docs(template())))
    over = main_container(gateway_deployment(docs(template(extra_files=[VALUES_PROD]))))
    assert base["image"] != over["image"], "prod overlay should change the image tag"
    assert (env_map(base)["GATEWAY_DATABASE_URL"].get("value")
            != env_map(over)["GATEWAY_DATABASE_URL"].get("value")), \
        "prod overlay should change the database URL"
    # M7 explicitly includes the secret ref — the JWT secretKeyRef name must swap.
    base_ref = env_map(base)["GATEWAY_JWT_SECRET"]["valueFrom"]["secretKeyRef"]["name"]
    over_ref = env_map(over)["GATEWAY_JWT_SECRET"]["valueFrom"]["secretKeyRef"]["name"]
    assert base_ref != over_ref, f"prod overlay must swap the jwt secret ref: {base_ref}=={over_ref}"


# ── M8 ──────────────────────────────────────────────────────────────────────────
def test_helpers_emit_stable_names():
    ds = docs(template())
    dep = gateway_deployment(ds)
    svc = by_kind(ds, "Service", "gateway")[0]
    assert dep["metadata"]["name"] == "ai-proxy-gateway", dep["metadata"]["name"]
    assert svc["metadata"]["name"] == "ai-proxy-gateway", svc["metadata"]["name"]
    sel = svc["spec"]["selector"]
    pod_labels = dep["spec"]["template"]["metadata"]["labels"]
    assert sel.items() <= pod_labels.items(), (sel, pod_labels)
    assert "app.kubernetes.io/name" in pod_labels


def test_service_dns_helper_is_correct():
    # M8: the in-cluster Service-DNS helper siblings consume must be exact. It is
    # surfaced as a Service annotation so a typo in the helper is caught here.
    ds = docs(template())
    svc = by_kind(ds, "Service", "gateway")[0]
    dns = svc["metadata"].get("annotations", {}).get("ai-proxy.dev/cluster-dns")
    assert dns == "ai-proxy-gateway.default.svc.cluster.local", dns


# ── R2 chart_invalid ────────────────────────────────────────────────────────────
def test_chart_invalid_fails():
    # A missing-required value: createSecret=true obliges gateway.jwtSecret.value
    # (the chart's `required` guard) — leaving it empty must fail the render.
    broken = template(set_args={"gateway.jwtSecret.createSecret": "true"})
    assert broken.returncode != 0, "a missing required value must fail the render"
    assert template().returncode == 0, "the default render must still succeed"


# ── R5 secret_ref_missing ───────────────────────────────────────────────────────
def test_production_requires_secret_ref():
    missing = template(set_args={
        "gateway.env.environment": "production",
        "gateway.jwtSecret.existingSecret": "",
        "gateway.jwtSecret.createSecret": "false",
    })
    assert missing.returncode != 0, "production without a jwt secret ref must fail"
    assert "secret_ref_missing" in (missing.stderr + missing.stdout)
    ok = template(set_args={
        "gateway.env.environment": "production",
        "gateway.jwtSecret.existingSecret": "my-secret",
    })
    assert ok.returncode == 0, ok.stderr


def test_secret_guard_fires_for_any_non_dev_env():
    # The guard must mirror the gateway's _forbid_dev_secret_outside_dev: it fires
    # for ANY environment outside {dev, test}, not just exact "production".
    for env in ("staging", "prod", "production"):
        proc = template(set_args={
            "gateway.env.environment": env,
            "gateway.jwtSecret.existingSecret": "",
            "gateway.jwtSecret.createSecret": "false",
        })
        assert proc.returncode != 0, f"guard must fire for env={env}"
        assert "secret_ref_missing" in (proc.stderr + proc.stdout)
    # dev/test are exempt (mirrors the app validator).
    for env in ("dev", "test"):
        proc = template(set_args={
            "gateway.env.environment": env,
            "gateway.jwtSecret.existingSecret": "",
            "gateway.jwtSecret.createSecret": "false",
        })
        assert proc.returncode == 0, f"guard must NOT fire for env={env}: {proc.stderr}"


def test_whitespace_secret_ref_is_rejected():
    # F6: a whitespace-only existingSecret is truthy in Go templates — the guard
    # must treat it as empty (else an invalid manifest renders with a blank name).
    proc = template(set_args={
        "gateway.env.environment": "production",
        "gateway.jwtSecret.existingSecret": "   ",
        "gateway.jwtSecret.createSecret": "false",
    })
    assert proc.returncode != 0, "whitespace-only secret ref must be rejected"
    assert "secret_ref_missing" in (proc.stderr + proc.stdout)
