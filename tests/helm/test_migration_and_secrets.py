"""Red/green contract tests for migrate-before-boot (v53 task 5).

Mechanism (Tin 2026-06-27): the gateway Deployment runs two ordered initContainers —
`wait-for-db` (bounded python socket loop) then `migrate` (`alembic upgrade head`) — BEFORE
the gateway container, so the gateway never serves an unmigrated DB. The migrate container
sources GATEWAY_DATABASE_URL identically to the gateway container (same Secret, no literal).
Plus the gateway IMAGE must carry the migration assets (Dockerfile COPY migrations/ + alembic.ini).

Harness mirrors the sibling helm suites: shell out to real `helm`, parse rendered YAML.
Red-for-the-right-reason: before the Dockerfile COPY + the initContainers exist, the source
check and the render assertions fail. `helm` is required (no skip).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
CHART = REPO / "charts" / "ai-proxy"
VALUES = CHART / "values.yaml"
GW = "ai-proxy-gateway"
DATASTORE_SECRET = "ai-proxy-datastore-secrets"
GW_DOCKERFILE = REPO / "apps" / "gateway" / "Dockerfile"

HELM = shutil.which("helm")


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    assert HELM, "helm binary required on PATH (brew install helm) — freeze decision"
    return subprocess.run([HELM, *args], cwd=str(REPO), capture_output=True, text=True)


def template(*, set_args: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    args = ["template", "ai-proxy", str(CHART)]
    for k, v in (set_args or {}).items():
        args += ["--set", f"{k}={v}"]
    return _helm(*args)


def docs(proc: subprocess.CompletedProcess[str]) -> list[dict]:
    assert proc.returncode == 0, f"helm template failed:\n{proc.stderr}"
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def gw_deploy(ds: list[dict]) -> dict:
    for d in ds:
        if d.get("kind") == "Deployment" and d.get("metadata", {}).get("name") == GW:
            return d
    raise AssertionError("gateway Deployment not found")


def pod_spec(dep: dict) -> dict:
    return dep["spec"]["template"]["spec"]


def init_containers(dep: dict) -> list[dict]:
    return pod_spec(dep).get("initContainers", [])


def by_name(containers: list[dict], name: str) -> dict | None:
    for c in containers:
        if c.get("name") == name:
            return c
    return None


def env_entry(container: dict, name: str) -> dict | None:
    for e in container.get("env", []):
        if e.get("name") == name:
            return e
    return None


# ── M1 / migration_assets_absent ──────────────────────────────────────────────────
def test_image_carries_migrations():
    df = GW_DOCKERFILE.read_text()
    assert "migrations/" in df and "COPY migrations" in df, "Dockerfile must COPY migrations/"
    assert "alembic.ini" in df and "COPY alembic.ini" in df, "Dockerfile must COPY alembic.ini"


# ── M2 ──────────────────────────────────────────────────────────────────────────
def test_initcontainers_before_gateway():
    dep = gw_deploy(docs(template(set_args={"gateway.env.databaseUrlSecretRef.name": DATASTORE_SECRET})))
    inits = init_containers(dep)
    names = [c["name"] for c in inits]
    assert names == ["wait-for-db", "migrate"], f"expected [wait-for-db, migrate], got {names}"
    img = "ai-proxy-gateway"
    for c in inits:
        assert img in c["image"], f"initContainer {c['name']} must use the gateway image, got {c['image']}"
    migrate = by_name(inits, "migrate")
    cmd = " ".join(migrate.get("command", []) + migrate.get("args", []))
    assert "alembic" in cmd and "upgrade" in cmd and "head" in cmd, f"migrate must run alembic upgrade head, got {cmd!r}"
    wait = by_name(inits, "wait-for-db")
    wcmd = " ".join(wait.get("command", []) + wait.get("args", []))
    assert "python" in wcmd, "wait-for-db must use python (guaranteed in image), not nc/pg_isready"
    assert "pg_isready" not in wcmd and "nc " not in wcmd, "wait-for-db must not rely on nc/pg_isready"
    # the gateway container still renders
    assert by_name(pod_spec(dep)["containers"], "gateway"), "gateway container must remain"


# ── M3 / migrate_dsn_mismatch ─────────────────────────────────────────────────────
def test_migrate_dsn_matches_gateway_secretref():
    dep = gw_deploy(docs(template(set_args={"gateway.env.databaseUrlSecretRef.name": DATASTORE_SECRET})))
    gw = by_name(pod_spec(dep)["containers"], "gateway")
    migrate = by_name(init_containers(dep), "migrate")
    gw_db = env_entry(gw, "GATEWAY_DATABASE_URL")
    mig_db = env_entry(migrate, "GATEWAY_DATABASE_URL")
    assert gw_db is not None and mig_db is not None
    # both source from the SAME secretKeyRef (name + key)
    assert gw_db == mig_db, f"migrate DSN must match the gateway's resolution:\n gw={gw_db}\n mig={mig_db}"
    assert mig_db.get("valueFrom", {}).get("secretKeyRef", {}).get("name") == DATASTORE_SECRET


def test_migrate_dsn_matches_gateway_literal():
    # default values: no secretRef → both use the SAME literal databaseUrl
    dep = gw_deploy(docs(template()))
    gw = by_name(pod_spec(dep)["containers"], "gateway")
    migrate = by_name(init_containers(dep), "migrate")
    gw_db = env_entry(gw, "GATEWAY_DATABASE_URL")
    mig_db = env_entry(migrate, "GATEWAY_DATABASE_URL")
    assert gw_db == mig_db, f"literal DSN must match:\n gw={gw_db}\n mig={mig_db}"
    assert "value" in mig_db, "default render uses a literal value (no secretRef)"


# ── M4 ──────────────────────────────────────────────────────────────────────────
def test_init_design_for_failure():
    dep = gw_deploy(docs(template()))
    inits = init_containers(dep)
    assert len(inits) == 2, f"expected wait-for-db + migrate initContainers, got {[c['name'] for c in inits]}"
    for c in inits:
        sc = c.get("securityContext", {})
        # inherits/mirrors the non-root pod securityContext (runAsNonRoot at pod or container)
        res = c.get("resources", {})
        assert res.get("requests") and res.get("limits"), f"{c['name']} needs resources requests+limits"
        _ = sc  # non-root enforced at pod level (podSecurityContext.runAsNonRoot)
    psc = pod_spec(dep).get("securityContext", {})
    assert psc.get("runAsNonRoot") is True, "pod runs non-root (covers the initContainers)"
    # gateway graceful-shutdown + probes untouched
    assert "terminationGracePeriodSeconds" in pod_spec(dep), "graceful drain must remain"
    gw = by_name(pod_spec(dep)["containers"], "gateway")
    assert gw.get("livenessProbe") and gw.get("readinessProbe") and gw.get("startupProbe"), "gateway probes intact"


# ── wait_unbounded ────────────────────────────────────────────────────────────────
def test_wait_is_bounded():
    dep = gw_deploy(docs(template(set_args={"gateway.migrate.waitForDb.maxAttempts": "7"})))
    wait = by_name(init_containers(dep), "wait-for-db")
    wcmd = " ".join(wait.get("command", []) + wait.get("args", []))
    # the configured bound must appear in the rendered command (no infinite loop)
    assert "7" in wcmd, f"wait-for-db must honor a bounded maxAttempts, got {wcmd!r}"


# ── M5 ──────────────────────────────────────────────────────────────────────────
def test_migrate_toggle():
    off = gw_deploy(docs(template(set_args={"gateway.migrate.enabled": "false"})))
    names = [c["name"] for c in init_containers(off)]
    assert "migrate" not in names, "migrate.enabled=false must drop the migrate initContainer"
    assert "wait-for-db" in names, "wait-for-db remains regardless of migrate toggle"
    # default ON
    on = gw_deploy(docs(template()))
    assert "migrate" in [c["name"] for c in init_containers(on)], "migrate is ON by default"


# ── secret_literal_in_chart ───────────────────────────────────────────────────────
def test_no_secret_literal_in_init():
    v = VALUES.read_text()
    out = template().stdout
    for marker in ("BEGIN CERTIFICATE", "BEGIN PRIVATE KEY", "password@", ":password"):
        assert marker not in v, f"values must ship no secret literal: {marker}"
    # the migrate env never inlines a DSN-with-password; default literal is passwordless
    dep = gw_deploy(docs(template()))
    migrate = by_name(init_containers(dep), "migrate")
    mig_db = env_entry(migrate, "GATEWAY_DATABASE_URL")
    val = mig_db.get("value", "")
    assert "@" not in val.split("//")[-1].split("/")[0] or ":" not in val.split("@")[0].split("//")[-1], (
        f"the default migrate DSN must be passwordless (no creds literal), got {val!r}"
    )
    _ = out


# ── migrate_after_boot ────────────────────────────────────────────────────────────
def test_migrate_is_initcontainer_only():
    dep = gw_deploy(docs(template()))
    # the migration runs ONLY as an initContainer — no sidecar / extra container runs alembic
    for c in pod_spec(dep)["containers"]:
        cmd = " ".join(c.get("command", []) + c.get("args", []))
        assert "alembic" not in cmd, f"alembic must not run in a long-lived container ({c['name']})"


# ── v2 F2: wait-for-db parses the DSN at runtime ──────────────────────────────────
def test_wait_parses_dsn():
    dep = gw_deploy(docs(template(set_args={"gateway.env.databaseUrlSecretRef.name": DATASTORE_SECRET})))
    wait = by_name(init_containers(dep), "wait-for-db")
    migrate = by_name(init_containers(dep), "migrate")
    # the wait carries the SAME DSN env as migrate (so it can read the real target)
    assert env_entry(wait, "GATEWAY_DATABASE_URL") == env_entry(migrate, "GATEWAY_DATABASE_URL")
    wcmd = " ".join(wait.get("command", []) + wait.get("args", []))
    assert "GATEWAY_DATABASE_URL" in wcmd, "wait must reference the DSN env"
    assert "environ" in wcmd, "wait must read the DSN from the environment at runtime"
    assert "urlparse" in wcmd or "urlsplit" in wcmd, "wait must parse host:port from the DSN"
    # external-DB path: postgres disabled + secretRef set → wait-for-db STILL renders (bounded)
    ext = gw_deploy(docs(template(set_args={
        "datastores.postgres.enabled": "false",
        "gateway.env.databaseUrlSecretRef.name": DATASTORE_SECRET,
    })))
    assert by_name(init_containers(ext), "wait-for-db") is not None, "external-DB path must still get a bounded wait"


# ── v2 F3: container-level securityContext on gateway + initContainers ─────────────
def test_gateway_pod_container_hardened():
    dep = gw_deploy(docs(template()))
    targets = [by_name(pod_spec(dep)["containers"], "gateway"), *init_containers(dep)]
    for c in targets:
        sc = c.get("securityContext", {})
        assert sc.get("allowPrivilegeEscalation") is False, f"{c['name']} needs allowPrivilegeEscalation:false"
        assert sc.get("capabilities", {}).get("drop") == ["ALL"], f"{c['name']} needs capabilities.drop=[ALL]"
    # pod-level non-root unchanged
    assert pod_spec(dep)["securityContext"].get("runAsUser") == 1000


# ── sibling suites stay green (chart still valid) ─────────────────────────────────
def test_chart_valid():
    assert template().returncode == 0, "default render must succeed"
    assert _helm("lint", str(CHART)).returncode == 0
