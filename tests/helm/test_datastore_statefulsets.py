"""Red/green contract tests for the ai-proxy datastore StatefulSets (v53 task 2).

Harness mirrors test_helm_chart_scaffold: shell out to the real `helm` binary,
parse rendered YAML with pyyaml, assert on the RENDERED objects (behavior), not
template text. One test per §2 scenario in `.add/tasks/datastore-statefulsets/TASK.md`.

Red-for-the-right-reason: before the datastore templates exist, `helm template`
renders no StatefulSets/Services/Job/Secret for Postgres/Redis/MinIO, so every
assertion here fails. After the build, every test goes green. `helm` is a hard
requirement (freeze decision) — the suite never skips on a missing binary.

Secret model (CR-1, secure-by-default): datastores.secrets.create defaults FALSE;
the default render REFERENCES an operator-provided Secret named
datastores.secrets.name (non-empty default) and therefore succeeds, exactly like
the gateway's jwtSecret.existingSecret. e2e/kind sets create=true + creds.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]  # tests/helm/ -> repo root
CHART = REPO / "charts" / "ai-proxy"
VALUES = CHART / "values.yaml"
RELEASE = "ai-proxy"

HELM = shutil.which("helm")

PG_SVC, REDIS_SVC, MINIO_SVC = "ai-proxy-postgres", "ai-proxy-redis", "ai-proxy-minio"


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    assert HELM, "helm binary required on PATH (brew install helm) — freeze decision"
    return subprocess.run([HELM, *args], cwd=str(REPO), capture_output=True, text=True)


def template(*, set_args: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    args = ["template", RELEASE, str(CHART)]
    for k, v in (set_args or {}).items():
        args += ["--set", f"{k}={v}"]
    return _helm(*args)


def docs(proc: subprocess.CompletedProcess[str]) -> list[dict]:
    assert proc.returncode == 0, f"helm template failed:\n{proc.stderr}"
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def by_kind(ds: list[dict], kind: str, name_has: str | None = None) -> list[dict]:
    out = [d for d in ds if d.get("kind") == kind]
    if name_has is not None:
        out = [d for d in out if name_has in d.get("metadata", {}).get("name", "")]
    return out


def one(ds: list[dict], kind: str, name_has: str) -> dict:
    got = by_kind(ds, kind, name_has)
    assert got, f"expected a {kind} matching {name_has!r}, got none"
    return got[0]


def container_of(workload: dict) -> dict:
    return workload["spec"]["template"]["spec"]["containers"][0]


def env_map(container: dict) -> dict[str, dict]:
    return {e["name"]: e for e in container.get("env", [])}


def cmdline(container: dict) -> str:
    return " ".join(container.get("command", []) + container.get("args", []))


def probe_cmd(container: dict, kind: str = "readinessProbe") -> str:
    probe = container.get(kind, {})
    return " ".join(probe.get("exec", {}).get("command", []))


def pinned_host(url: str) -> str:
    """Extract the host the gateway's default conn-string dials (e.g. ai-proxy-postgres)."""
    after = url.split("//", 1)[-1].split("/", 1)[0]
    return after.split("@")[-1].split(":")[0]


def secret_ref(env_entry: dict) -> dict:
    return env_entry.get("valueFrom", {}).get("secretKeyRef", {})


# ── M1: Postgres StatefulSet renders durably ───────────────────────────────────
def test_postgres_statefulset_renders_durably():
    ds = docs(template())
    ss = one(ds, "StatefulSet", PG_SVC)
    c = container_of(ss)
    assert c["image"] == "postgres:16-alpine", c["image"]
    svc = one(ds, "Service", PG_SVC)
    assert svc["spec"].get("clusterIP") == "None", "Postgres Service must be headless"
    assert 5432 in [p["port"] for p in svc["spec"]["ports"]]
    assert ss["spec"].get("volumeClaimTemplates"), "Postgres needs a PVC (durability)"
    assert "pg_isready" in probe_cmd(c), probe_cmd(c)
    pw = env_map(c)["POSTGRES_PASSWORD"]
    assert "value" not in pw, "POSTGRES_PASSWORD must not be a literal"
    assert secret_ref(pw), pw


# ── M2: Redis StatefulSet renders durably, no-auth ─────────────────────────────
def test_redis_statefulset_renders_durably():
    ds = docs(template())
    ss = one(ds, "StatefulSet", REDIS_SVC)
    c = container_of(ss)
    assert c["image"] == "redis:7-alpine", c["image"]
    svc = one(ds, "Service", REDIS_SVC)
    assert 6379 in [p["port"] for p in svc["spec"]["ports"]]
    assert ss["spec"].get("volumeClaimTemplates"), "Redis needs a PVC"
    rp = probe_cmd(c)
    assert "redis-cli" in rp and "ping" in rp, rp
    # No-auth in-cluster: no requirepass / AUTH env, no --requirepass in the cmd.
    assert "REDIS_PASSWORD" not in env_map(c)
    assert "requirepass" not in cmdline(c).lower(), cmdline(c)


# ── M3: MinIO StatefulSet renders durably ──────────────────────────────────────
def test_minio_statefulset_renders_durably():
    ds = docs(template())
    ss = one(ds, "StatefulSet", MINIO_SVC)
    c = container_of(ss)
    assert "minio/minio" in c["image"], c["image"]
    line = cmdline(c)
    assert "server" in line and "/data" in line, line
    assert "--console-address" in line and ":9001" in line, line
    svc = one(ds, "Service", MINIO_SVC)
    ports = [p["port"] for p in svc["spec"]["ports"]]
    assert 9000 in ports and 9001 in ports, ports
    assert ss["spec"].get("volumeClaimTemplates"), "MinIO needs a PVC"
    rp = c.get("readinessProbe", {}).get("httpGet", {})
    assert rp.get("path") == "/minio/health/ready", rp
    env = env_map(c)
    for key in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"):
        assert "value" not in env[key], f"{key} must not be a literal"
        assert secret_ref(env[key]), env[key]


# ── M4: MinIO bucket-create Job is a post-install hook ─────────────────────────
def test_minio_bucket_job_is_post_install_hook():
    ds = docs(template())
    job = one(ds, "Job", "minio-createbucket")
    ann = job["metadata"].get("annotations", {})
    hook = ann.get("helm.sh/hook", "")
    assert "post-install" in hook and "post-upgrade" in hook, hook
    # before-hook-creation is required so a failed prior Job never blocks re-deploy (F8).
    assert "before-hook-creation" in ann.get("helm.sh/hook-delete-policy", ""), ann
    c = container_of(job)
    assert "minio/mc" in c["image"], c["image"]
    # Bucket name is values-driven and ties to the gateway's configured bucket.
    sentinel = docs(template(set_args={"gateway.objectStore.bucket": "sentinel-bucket-xyz"}))
    sj = container_of(one(sentinel, "Job", "minio-createbucket"))
    assert "sentinel-bucket-xyz" in cmdline(sj), cmdline(sj)
    # Default render (gateway.objectStore.bucket="") falls back to a conventional name (F5).
    assert "ai-proxy-artifacts" in cmdline(c), cmdline(c)


# ── M5: Datastore credentials come from a Secret, not values ───────────────────
def test_datastore_credentials_from_secret():
    # create=true + operator creds → a Secret carrying the four keys renders.
    ds = docs(template(set_args={
        "datastores.secrets.create": "true",
        "datastores.secrets.postgresPassword": "pg-sentinel-123",
        "datastores.secrets.minioRootUser": "root-sentinel",
        "datastores.secrets.minioRootPassword": "root-pw-sentinel",
    }))
    sec = one(ds, "Secret", "datastore-secrets")
    blob = {**sec.get("data", {}), **sec.get("stringData", {})}
    for key in ("pg-password", "url", "minio-root-user", "minio-root-password"):
        assert key in blob, f"datastore Secret missing key {key}: {list(blob)}"
        assert str(blob[key]).strip(), f"datastore Secret key {key} rendered empty"
    # The supplied password actually flows into both the key and the assembled DSN.
    assert "pg-sentinel-123" in str(blob["pg-password"])
    assert "pg-sentinel-123" in str(blob["url"]), "DSN must carry the supplied password"
    # The bare default render (create=false) ships NO populated datastore Secret.
    default = docs(template())
    populated = [s for s in by_kind(default, "Secret", "datastore-secrets")
                 if s.get("data") or s.get("stringData")]
    assert populated == [], f"default must ship no datastore Secret material: {populated}"


def test_create_true_requires_nonempty_creds():
    # F1 fail-closed: create=true with an empty password must NOT render a passwordless
    # datastore — the guard fails the render rather than shipping `gateway:@host`.
    empty_pg = template(set_args={
        "datastores.secrets.create": "true",
        "datastores.secrets.postgresPassword": "",
        "datastores.secrets.minioRootUser": "root",
        "datastores.secrets.minioRootPassword": "rootpw",
    })
    assert empty_pg.returncode != 0, "create=true with empty postgresPassword must fail"
    assert "datastore_secret_value_missing" in (empty_pg.stderr + empty_pg.stdout)
    empty_minio = template(set_args={
        "datastores.secrets.create": "true",
        "datastores.secrets.postgresPassword": "pw",
        "datastores.secrets.minioRootUser": "root",
        "datastores.secrets.minioRootPassword": "",
    })
    assert empty_minio.returncode != 0, "create=true with empty minioRootPassword must fail"
    # All creds supplied → renders cleanly.
    ok = template(set_args={
        "datastores.secrets.create": "true",
        "datastores.secrets.postgresPassword": "pw",
        "datastores.secrets.minioRootUser": "root",
        "datastores.secrets.minioRootPassword": "rootpw",
    })
    assert ok.returncode == 0, ok.stderr


# ── M6: external managed datastore — render nothing in-cluster ─────────────────
def test_external_managed_renders_nothing():
    ds = docs(template(set_args={"datastores.postgres.enabled": "false"}))
    assert not by_kind(ds, "StatefulSet", PG_SVC), "disabled Postgres must not render a StatefulSet"
    assert not by_kind(ds, "Service", PG_SVC), "disabled Postgres must not render a Service"
    # Siblings + gateway untouched.
    assert by_kind(ds, "StatefulSet", REDIS_SVC) and by_kind(ds, "StatefulSet", MINIO_SVC)
    assert by_kind(ds, "Deployment", "gateway"), "gateway must be unaffected"
    # All datastores disabled → zero datastore StatefulSets, gateway still renders.
    none = docs(template(set_args={
        "datastores.postgres.enabled": "false",
        "datastores.redis.enabled": "false",
        "datastores.minio.enabled": "false",
    }))
    assert by_kind(none, "StatefulSet") == [], "no datastore StatefulSets when all disabled"
    assert by_kind(none, "Deployment", "gateway"), "gateway must still render"


# ── M7: Service names match the gateway-pinned hosts ───────────────────────────
def test_service_names_match_gateway_pins():
    v = yaml.safe_load(VALUES.read_text())
    assert pinned_host(v["gateway"]["env"]["databaseUrl"]) == PG_SVC
    assert pinned_host(v["gateway"]["env"]["redisUrl"]) == REDIS_SVC
    ds = docs(template())
    names = {s["metadata"]["name"] for s in by_kind(ds, "Service")}
    assert {PG_SVC, REDIS_SVC, MINIO_SVC} <= names, names


# ── secret_literal_forbidden ───────────────────────────────────────────────────
def test_no_credential_literal():
    ds = docs(template())
    for svc_name, keys in (
        (PG_SVC, ["POSTGRES_PASSWORD"]),
        (MINIO_SVC, ["MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"]),
    ):
        c = container_of(one(ds, "StatefulSet", svc_name))
        env = env_map(c)
        for key in keys:
            assert "value" not in env[key], f"{key} is a plaintext literal"
            assert secret_ref(env[key]), f"{key} must use secretKeyRef"
    # The bucket Job's MinIO creds must be secretKeyRef-only too (F4 — was untested).
    jenv = env_map(container_of(one(ds, "Job", "minio-createbucket")))
    for key in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"):
        assert "value" not in jenv[key], f"Job {key} is a plaintext literal"
        assert secret_ref(jenv[key]), f"Job {key} must use secretKeyRef"


# ── chart_invalid ──────────────────────────────────────────────────────────────
def test_chart_invalid_fails():
    # Blanking the secret reference while a datastore is enabled (non-dev env) must fail.
    broken = template(set_args={
        "datastores.secrets.name": "",
        "gateway.env.environment": "production",
    })
    assert broken.returncode != 0, "blank datastore secret name must fail the render"
    assert "datastore_secret_ref_missing" in (broken.stderr + broken.stdout)
    # The default render stays valid + lints clean.
    assert template().returncode == 0, "default render must still succeed"
    assert _helm("lint", str(CHART)).returncode == 0


# ── durability_incomplete ──────────────────────────────────────────────────────
def test_durability_complete():
    ds = docs(template())
    for name in (PG_SVC, REDIS_SVC, MINIO_SVC):
        ss = one(ds, "StatefulSet", name)
        assert ss["spec"].get("volumeClaimTemplates"), f"{name} missing PVC"
        assert container_of(ss).get("readinessProbe"), f"{name} missing readiness probe"


# ── design-for-failure: resource requests AND limits (parity with the gateway) ──
def test_datastore_resources_complete():
    ds = docs(template())
    for name in (PG_SVC, REDIS_SVC, MINIO_SVC):
        res = container_of(one(ds, "StatefulSet", name)).get("resources", {})
        for tier in ("requests", "limits"):
            assert res.get(tier, {}).get("cpu"), f"{name} missing resources.{tier}.cpu"
            assert res.get(tier, {}).get("memory"), f"{name} missing resources.{tier}.memory"


# ── bucket_create_not_idempotent ───────────────────────────────────────────────
def test_bucket_job_idempotent():
    job = one(docs(template()), "Job", "minio-createbucket")
    line = cmdline(container_of(job))
    assert "mb" in line, line
    assert "ignore-existing" in line or "true" in line, \
        f"bucket create must be idempotent (mc mb --ignore-existing or mb||true): {line}"


# ── service_name_mismatch ──────────────────────────────────────────────────────
def test_service_name_mismatch_guarded():
    v = yaml.safe_load(VALUES.read_text())
    ds = docs(template())
    # Each datastore Service name is byte-identical to the gateway's pinned host.
    assert one(ds, "Service", PG_SVC)["metadata"]["name"] == pinned_host(v["gateway"]["env"]["databaseUrl"])
    assert one(ds, "Service", REDIS_SVC)["metadata"]["name"] == pinned_host(v["gateway"]["env"]["redisUrl"])
    assert one(ds, "Service", MINIO_SVC)["metadata"]["name"] == MINIO_SVC
