"""Red/green contract tests for the ai-proxy dashboard chart + edge route-split (v53 task 4).

Harness mirrors the sibling helm suites: shell out to real `helm`, parse rendered YAML
with pyyaml, assert on RENDERED objects (behavior). Two surfaces are tested:
  1. the dashboard k8s objects (Deployment/Service/PDB) filling the frozen `dashboard{}` block,
  2. the ADDITIVE Envoy route-split (dashboard_cluster + the `/`-route flip), gated on
     dashboard.enabled — without disturbing task-3's frozen auth posture (/v1/ ext_authz ON).
Plus file-parse checks of the real image shape (next.config.ts standalone, the Dockerfile,
the new /api/health route), which only prove at build time, not at container start.

Red-for-the-right-reason: before the dashboard templates/files exist, no dashboard objects
render and the source files are absent, so every assertion fails. `helm` is required (no skip).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
CHART = REPO / "charts" / "ai-proxy"
VALUES = CHART / "values.yaml"
DASH = "ai-proxy-dashboard"
GATEWAY_SVC = "ai-proxy-gateway"
ENVOY = "ai-proxy-envoy"
APP = REPO / "apps" / "dashboard"

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


def by_kind(ds: list[dict], kind: str, name_has: str | None = None) -> list[dict]:
    out = [d for d in ds if d.get("kind") == kind]
    if name_has is not None:
        out = [d for d in out if name_has in d.get("metadata", {}).get("name", "")]
    return out


def one(ds: list[dict], kind: str, name_has: str) -> dict:
    got = by_kind(ds, kind, name_has)
    assert got, f"expected a {kind} matching {name_has!r}, got none"
    return got[0]


def dash_deployment(ds: list[dict]) -> dict:
    return one(ds, "Deployment", DASH)


def container(dep: dict) -> dict:
    return dep["spec"]["template"]["spec"]["containers"][0]


def env_map(dep: dict) -> dict:
    return {e["name"]: e for e in container(dep).get("env", [])}


def ports_by_name(dep: dict) -> dict:
    return {p.get("name"): p["containerPort"] for p in container(dep).get("ports", [])}


# ── envoy config parsing (shared shape with the task-3 suite) ─────────────────────
def envoy_config(ds: list[dict]) -> dict:
    cm = one(ds, "ConfigMap", ENVOY)
    return yaml.safe_load(cm["data"]["envoy.yaml"])


def tls_routes(cfg: dict) -> dict:
    lis = [l for l in cfg["static_resources"]["listeners"] if l["name"] == "listener_tls"][0]
    hcm = lis["filter_chains"][0]["filters"][0]["typed_config"]
    return {r["match"].get("prefix"): r for r in hcm["route_config"]["virtual_hosts"][0]["routes"]}


def cluster(cfg: dict, name: str) -> dict | None:
    for c in cfg["static_resources"]["clusters"]:
        if c["name"] == name:
            return c
    return None


def cluster_host(c: dict) -> tuple[str, int]:
    sa = c["load_assignment"]["endpoints"][0]["lb_endpoints"][0]["endpoint"]["address"]["socket_address"]
    return sa["address"], sa["port_value"]


def route_ext_authz(route: dict) -> dict:
    return route.get("typed_per_filter_config", {}).get("envoy.filters.http.ext_authz", {})


# ── M1 ──────────────────────────────────────────────────────────────────────────
def test_standalone_image():
    cfg = (APP / "next.config.ts").read_text()
    assert "output:" in cfg and "standalone" in cfg, "next.config.ts must set output:'standalone'"
    df = (APP / "Dockerfile").read_text()
    assert "FROM" in df and df.count("FROM") >= 2, "Dockerfile must be multi-stage"
    assert "node" in df and "server.js" in df, "runtime CMD must run node server.js"
    assert ".next/standalone" in df and ".next/static" in df, "must copy the standalone + static output"
    assert "next start" not in df, "must NOT use `next start`"
    assert "USER" in df, "must declare a non-root USER"
    assert "3000" in df, "must expose/serve the dashboard port"


# ── M2 ──────────────────────────────────────────────────────────────────────────
def test_deployment_wires_gateway():
    ds = docs(template())
    dep = dash_deployment(ds)
    img = container(dep)["image"]
    assert "ai-proxy-dashboard" in img, f"dashboard must use its own image, got {img!r}"
    assert "ai-proxy-gateway" not in img, "dashboard must NOT run the gateway image"
    env = env_map(dep)
    assert env["GATEWAY_URL"]["value"] == "http://ai-proxy-gateway:8000", env["GATEWAY_URL"]
    assert env["GATEWAY_PROXY_TIMEOUT_MS"]["value"] == "15000", env["GATEWAY_PROXY_TIMEOUT_MS"]
    assert env["GW_MAX_BODY_BYTES"]["value"] == "33554432", env["GW_MAX_BODY_BYTES"]


def test_gateway_url_in_cluster():
    env = env_map(dash_deployment(docs(template())))
    url = env["GATEWAY_URL"]["value"]
    assert url == "http://ai-proxy-gateway:8000", url
    for bad in ("localhost", "127.0.0.1", ":8443", "https://"):
        assert bad not in url, f"GATEWAY_URL must be the in-cluster Service, not {bad!r}"
    # an operator override still renders (managed gateway)
    over = dash_deployment(docs(template(set_args={"dashboard.env.gatewayUrl": "http://mygw:9000"})))
    assert env_map(over)["GATEWAY_URL"]["value"] == "http://mygw:9000"


# ── M3 ──────────────────────────────────────────────────────────────────────────
def test_service():
    ds = docs(template())
    svc = one(ds, "Service", DASH)
    assert svc["spec"]["type"] == "ClusterIP"
    port = svc["spec"]["ports"][0]
    assert port["port"] == 3000, port
    assert port["targetPort"] in ("http", 3000), port
    assert svc["spec"]["selector"].get("app.kubernetes.io/component") == "dashboard"


# ── M4 ──────────────────────────────────────────────────────────────────────────
def test_design_for_failure():
    ds = docs(template())
    dep = dash_deployment(ds)
    c = container(dep)
    for probe in ("livenessProbe", "readinessProbe", "startupProbe"):
        http = c[probe]["httpGet"]
        assert http["path"] == "/api/health", (probe, http)
        assert http["port"] in ("http", 3000), (probe, http)
    res = c["resources"]
    for tier in ("requests", "limits"):
        assert res[tier]["cpu"] and res[tier]["memory"], tier
    sec = dep["spec"]["template"]["spec"]["securityContext"]
    assert sec.get("runAsNonRoot") is True, sec
    assert by_kind(ds, "PodDisruptionBudget", DASH), "expected a dashboard PDB"
    # the health route exists and returns 200 without auth/DB
    route = (APP / "app" / "api" / "health" / "route.ts").read_text()
    assert "200" in route, "health route must return 200"
    assert "cookie" not in route.lower() and "fetch(" not in route, "health must not auth/DB"


def test_probe_target_safe():
    c = container(dash_deployment(docs(template())))
    for probe in ("livenessProbe", "readinessProbe", "startupProbe"):
        path = c[probe]["httpGet"]["path"]
        assert path == "/api/health", f"{probe} must hit /api/health, not {path!r}"


# ── M5 ──────────────────────────────────────────────────────────────────────────
def test_edge_route_split():
    cfg = envoy_config(docs(template()))
    dc = cluster(cfg, "dashboard_cluster")
    assert dc is not None, "expected a dashboard_cluster when dashboard.enabled"
    host, port = cluster_host(dc)
    assert host == DASH and port == 3000, (host, port)
    routes = tls_routes(cfg)
    assert routes["/"]["route"]["cluster"] == "dashboard_cluster", routes["/"]["route"]
    assert route_ext_authz(routes["/"]).get("disabled") is True, "dashboard `/` keeps ext_authz disabled"
    assert routes["/v1/"]["route"]["cluster"] == "gateway_cluster"
    assert "check_settings" in route_ext_authz(routes["/v1/"]), "/v1/ ext_authz must stay ON"
    assert routes["/admin/"]["route"]["cluster"] == "gateway_cluster"
    assert routes["/internal/"]["direct_response"]["status"] == 403


def test_edge_split_authz_intact():
    # /v1/ ext_authz ON regardless of dashboard.enabled; `/` never -> gateway when enabled
    on = envoy_config(docs(template()))
    assert "check_settings" in route_ext_authz(tls_routes(on)["/v1/"])
    assert tls_routes(on)["/"]["route"]["cluster"] == "dashboard_cluster"
    off = envoy_config(docs(template(set_args={"dashboard.enabled": "false"})))
    assert "check_settings" in route_ext_authz(tls_routes(off)["/v1/"])


# ── M6 ──────────────────────────────────────────────────────────────────────────
def test_external_ready_frozen():
    ds = docs(template(set_args={"dashboard.enabled": "false"}))
    assert not by_kind(ds, "Deployment", DASH)
    assert not by_kind(ds, "Service", DASH)
    assert not by_kind(ds, "PodDisruptionBudget", DASH)
    cfg = envoy_config(ds)
    assert cluster(cfg, "dashboard_cluster") is None, "no dashboard_cluster when disabled"
    assert tls_routes(cfg)["/"]["route"]["cluster"] == "gateway_cluster", "`/` reverts to gateway"
    # gateway + datastores untouched
    assert by_kind(ds, "Deployment", GATEWAY_SVC), "gateway must be unaffected"
    assert by_kind(ds, "StatefulSet", "ai-proxy-postgres"), "datastores must be unaffected"


# ── chart_invalid + frozen_schema_violation ──────────────────────────────────────
def test_chart_valid():
    assert template().returncode == 0, "default render must succeed"
    assert _helm("lint", str(CHART)).returncode == 0


def test_no_secret_no_frozen_touch():
    v = VALUES.read_text()
    full = template().stdout
    for marker in ("BEGIN CERTIFICATE", "BEGIN PRIVATE KEY", "BEGIN RSA"):
        assert marker not in v and marker not in full, f"no secret material: {marker}"
    vy = yaml.safe_load(v)
    # the dashboard sub-tree carries no inline password/secret value
    dash = vy["dashboard"]
    assert "password" not in yaml.safe_dump(dash).lower(), "dashboard ships no secret"
    # task-3 envoy auth posture preserved (the frozen-posture guard, redundant-by-design):
    routes = tls_routes(envoy_config(docs(template())))
    assert "check_settings" in route_ext_authz(routes["/v1/"]), "/v1/ ext_authz must stay ON"
    assert routes["/internal/"]["direct_response"]["status"] == 403, "/internal/ must stay 403"
    assert route_ext_authz(routes["/admin/"]).get("disabled") is True, "/admin/ ext_authz unchanged"


# ── pod_hardened (M4 hardening — refute-read v2) ──────────────────────────────────
def test_pod_hardened():
    ds = docs(template())
    c = container(dash_deployment(ds))
    sc = c.get("securityContext", {})
    assert sc.get("allowPrivilegeEscalation") is False, sc
    assert sc.get("capabilities", {}).get("drop") == ["ALL"], sc
    np = one(ds, "NetworkPolicy", DASH)
    assert set(np["spec"]["policyTypes"]) == {"Ingress", "Egress"}, np["spec"]["policyTypes"]
    assert np["spec"]["podSelector"]["matchLabels"].get("app.kubernetes.io/component") == "dashboard"
    # ingress only from envoy pods on :3000
    ing = np["spec"]["ingress"]
    ing_ports = {p["port"] for rule in ing for p in rule.get("ports", [])}
    assert 3000 in ing_ports, ing_ports
    ing_from = [f for rule in ing for f in rule.get("from", [])]
    assert any(f.get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/component") == "envoy"
               for f in ing_from), "ingress must come from envoy pods"
    # egress only to the gateway (:8000) + DNS (:53)
    eg_ports = {p["port"] for rule in np["spec"]["egress"] for p in rule.get("ports", [])}
    assert 8000 in eg_ports and 53 in eg_ports, eg_ports
    eg_to = [t for rule in np["spec"]["egress"] for t in rule.get("to", [])]
    assert any(t.get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/component") == "gateway"
               for t in eg_to), "egress must reach the gateway pods"
    # toggle off → no NetworkPolicy
    off = docs(template(set_args={"dashboard.networkPolicy.enabled": "false"}))
    assert not by_kind(off, "NetworkPolicy", DASH), "networkPolicy.enabled=false renders none"


# ── server_resolver_reads_public_var (BFF footgun — refute-read v3) ───────────────
# The 6 SERVER-side BFF route handlers that resolve the in-cluster gateway URL. A
# NEXT_PUBLIC_-prefixed env is inlined into the CLIENT bundle at build, so a server
# resolver reading it invites operators to set it → leaks the in-cluster gateway
# address to browsers. Server code must read ONLY the non-public GATEWAY_URL.
BFF_RESOLVER_ROUTES = [
    "app/api/gw/[...path]/route.ts",
    "app/api/auth/login/route.ts",
    "app/api/auth/signup/route.ts",
    "app/api/auth/me/route.ts",
    "app/api/auth/oidc/login/route.ts",
    "app/auth/oidc/callback/route.ts",
]


def test_bff_no_public_gateway_var():
    for rel in BFF_RESOLVER_ROUTES:
        src = (APP / rel).read_text()
        assert "NEXT_PUBLIC_GATEWAY_URL" not in src, (
            f"{rel} must NOT read a NEXT_PUBLIC_-prefixed gateway var "
            "(it would inline the in-cluster gateway URL into the client bundle)"
        )
        # the resolver still works server-side: GATEWAY_URL + the localhost default
        assert "GATEWAY_URL" in src, f"{rel} must still resolve the non-public GATEWAY_URL"
        assert "localhost:8080" in src, f"{rel} must keep the local-dev default"


# ── pod_not_pss_restricted (PSS-restricted hardening — Tin gate v4) ────────────────
def test_pod_pss_restricted():
    ds = docs(template())
    dep = dash_deployment(ds)
    pod = dep["spec"]["template"]["spec"]
    # no service-account token (the dashboard never calls the k8s API)
    assert pod.get("automountServiceAccountToken") is False, pod.get("automountServiceAccountToken")
    # pod-level: explicit non-root group + fsGroup (emptyDir ownership) + seccomp
    psc = pod["securityContext"]
    assert psc.get("runAsGroup") == 1000, psc
    assert psc.get("fsGroup") == 1000, psc
    assert psc.get("seccompProfile", {}).get("type") == "RuntimeDefault", psc
    # container-level: immutable rootfs + non-root + container seccomp
    csc = container(dep).get("securityContext", {})
    assert csc.get("readOnlyRootFilesystem") is True, csc
    assert csc.get("runAsNonRoot") is True, csc
    assert csc.get("seccompProfile", {}).get("type") == "RuntimeDefault", csc
    # writable paths under RO-rootfs: emptyDir volumes + matching mounts for /tmp + Next cache
    vols = {v["name"]: v for v in pod.get("volumes", [])}
    mounts = {m["mountPath"]: m["name"] for m in container(dep).get("volumeMounts", [])}
    assert "/tmp" in mounts, mounts
    assert "/app/.next/cache" in mounts, mounts
    for mp in ("/tmp", "/app/.next/cache"):
        vname = mounts[mp]
        assert "emptyDir" in vols[vname], f"{mp} must be backed by an emptyDir, got {vols[vname]}"
