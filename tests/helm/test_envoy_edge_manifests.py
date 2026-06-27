"""Red/green contract tests for the ai-proxy Envoy edge (v53 task 3).

Harness mirrors the other helm suites: shell out to real `helm`, parse rendered
YAML with pyyaml, assert on RENDERED objects (behavior). The Envoy config rides in
a ConfigMap as a FULLY Helm-TEMPLATED `envoy.yaml` string — these tests parse that
inner YAML and assert the filter chain is faithful to the proven infra/envoy/envoy-prod.yaml
(filters in order, per-route ext_authz toggles, WS upgrade, upstream host) and that the
JWT secret never lands in the ConfigMap (initContainer substitutes it out-of-band).

Red-for-the-right-reason: before the envoy templates exist, no Envoy ConfigMap/Deployment/
Service renders, so every assertion fails. `helm` is a hard requirement (no skip).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
CHART = REPO / "charts" / "ai-proxy"
VALUES = CHART / "values.yaml"
RELEASE = "ai-proxy"
ENVOY = "ai-proxy-envoy"
GATEWAY_SVC = "ai-proxy-gateway"
# The envoy STRICT_DNS cluster must dial the gateway by FQDN — envoy's c-ares resolver does not
# apply the k8s resolv.conf search domains, so a bare service name never resolves (v53 kind-bootstrap).
GATEWAY_FQDN = "ai-proxy-gateway.default.svc.cluster.local"

HELM = shutil.which("helm")


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


def envoy_config(ds: list[dict]) -> tuple[dict, str]:
    """Parse the templated envoy.yaml carried in the ConfigMap."""
    cm = one(ds, "ConfigMap", ENVOY)
    raw = cm["data"]["envoy.yaml"]
    return yaml.safe_load(raw), raw


def tls_hcm(cfg: dict) -> dict:
    lis = [l for l in cfg["static_resources"]["listeners"] if l["name"] == "listener_tls"][0]
    return lis["filter_chains"][0]["filters"][0]["typed_config"]


def listener_ports(cfg: dict) -> set[int]:
    return {l["address"]["socket_address"]["port_value"]
            for l in cfg["static_resources"]["listeners"]}


def gateway_endpoint(cfg: dict) -> dict:
    cl = [c for c in cfg["static_resources"]["clusters"] if c["name"] == "gateway_cluster"][0]
    return (cl["load_assignment"]["endpoints"][0]["lb_endpoints"][0]
            ["endpoint"]["address"]["socket_address"])


def envoy_deployment(ds: list[dict]) -> dict:
    return one(ds, "Deployment", ENVOY)


def containers(dep: dict) -> list[dict]:
    return dep["spec"]["template"]["spec"]["containers"]


def init_containers(dep: dict) -> list[dict]:
    return dep["spec"]["template"]["spec"].get("initContainers", [])


def route_ext_authz(route: dict) -> dict:
    return route.get("typed_per_filter_config", {}).get("envoy.filters.http.ext_authz", {})


# ── M1 ──────────────────────────────────────────────────────────────────────────
def test_envoy_edge_renders():
    ds = docs(template())
    dep = envoy_deployment(ds)
    img = containers(dep)[0]["image"]
    assert img.startswith("envoyproxy/envoy:v1.29-latest@sha256:"), img
    assert by_kind(ds, "Service", ENVOY), "expected an Envoy Service"
    assert by_kind(ds, "ConfigMap", ENVOY), "expected an Envoy ConfigMap"


def _assert_digest_pinned(image: str, repo_tag: str):
    assert "@sha256:" in image, f"{image!r} must be digest-pinned"
    base, _, digest = image.partition("@sha256:")
    assert base == repo_tag, f"expected repo:tag {repo_tag!r}, got {base!r}"
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), digest


def test_images_digest_pinned():
    ds = docs(template())
    dep = envoy_deployment(ds)
    _assert_digest_pinned(containers(dep)[0]["image"], "envoyproxy/envoy:v1.29-latest")
    _assert_digest_pinned(init_containers(dep)[0]["image"], "busybox:1.36")
    # still operator-overridable
    over = docs(template(set_args={
        "envoy.image": "myrepo/envoy:pinned@sha256:" + "a" * 64,
        "envoy.initImage": "myrepo/busybox:x@sha256:" + "b" * 64,
    }))
    odep = envoy_deployment(over)
    assert containers(odep)[0]["image"].startswith("myrepo/envoy:pinned@sha256:")
    assert init_containers(odep)[0]["image"].startswith("myrepo/busybox:x@sha256:")


# ── M2 ──────────────────────────────────────────────────────────────────────────
def test_filter_chain_faithful():
    cfg, _ = envoy_config(docs(template()))
    hcm = tls_hcm(cfg)
    names = [f["name"] for f in hcm["http_filters"]]
    assert names == [
        "envoy.filters.http.local_ratelimit",
        "envoy.filters.http.jwt_authn",
        "envoy.filters.http.ext_authz",
        "envoy.filters.http.router",
    ], names
    assert hcm["upgrade_configs"][0]["upgrade_type"] == "websocket"
    # ext_authz auth posture is security-critical — assert it explicitly, not just by presence.
    ea = next(f for f in hcm["http_filters"] if f["name"].endswith("ext_authz"))["typed_config"]
    assert ea["failure_mode_allow"] is False, "ext_authz must fail CLOSED (never fail-open auth)"
    assert ea["http_service"]["path_prefix"] == "/internal/authz", ea["http_service"]["path_prefix"]
    sent = {p["exact"] for p in ea["http_service"]["authorization_request"]["allowed_headers"]["patterns"]}
    assert {"authorization", "x-api-key"} <= sent, sent
    got = {p["exact"] for p in ea["http_service"]["authorization_response"]["allowed_upstream_headers"]["patterns"]}
    assert {"x-tenant-id", "x-key-id"} <= got, got
    # Route table: ext_authz enabled on /v1/, disabled on /admin/ + /, 403 on /internal/.
    routes = {r["match"].get("prefix"): r for r in hcm["route_config"]["virtual_hosts"][0]["routes"]}
    assert "check_settings" in route_ext_authz(routes["/v1/"]), "ext_authz must be enabled on /v1/"
    assert route_ext_authz(routes["/admin/"]).get("disabled") is True
    assert route_ext_authz(routes["/"]).get("disabled") is True
    assert routes["/internal/"]["direct_response"]["status"] == 403
    assert listener_ports(cfg) == {8443, 8080, 9902}, listener_ports(cfg)
    assert cfg["admin"]["address"]["socket_address"]["port_value"] == 9901


# ── M3 ──────────────────────────────────────────────────────────────────────────
def test_magic_numbers_values_driven():
    cfg, _ = envoy_config(docs(template(set_args={
        "envoy.rateLimit.maxTokens": "7",
        "envoy.extAuthz.timeout": "9s",
        "envoy.hsts.maxAge": "11",
        "envoy.jwt.issuer": "acme",
    })))
    hcm = tls_hcm(cfg)
    rl = next(f for f in hcm["http_filters"] if f["name"].endswith("local_ratelimit"))
    assert rl["typed_config"]["token_bucket"]["max_tokens"] == 7
    ea = next(f for f in hcm["http_filters"] if f["name"].endswith("ext_authz"))
    assert ea["typed_config"]["http_service"]["server_uri"]["timeout"] == "9s"
    jwt = next(f for f in hcm["http_filters"] if f["name"].endswith("jwt_authn"))
    assert jwt["typed_config"]["providers"]["gateway_jwt"]["issuer"] == "acme"
    hsts = hcm["route_config"]["response_headers_to_add"][0]["header"]["value"]
    assert "max-age=11" in hsts, hsts
    # Defaults hold + upstream host is the gateway Service (never literal "gateway").
    default_cfg, _ = envoy_config(docs(template()))
    dhcm = tls_hcm(default_cfg)
    drl = next(f for f in dhcm["http_filters"] if f["name"].endswith("local_ratelimit"))
    assert drl["typed_config"]["token_bucket"]["max_tokens"] == 50
    ep = gateway_endpoint(default_cfg)
    assert ep["address"] == GATEWAY_FQDN and ep["port_value"] == 8000, ep


# ── M4 ──────────────────────────────────────────────────────────────────────────
def test_jwt_secret_out_of_band():
    ds = docs(template())
    _, raw = envoy_config(ds)
    assert "GATEWAY_JWT_SECRET_BASE64URL" in raw, "ConfigMap must keep the JWKS placeholder"
    dep = envoy_deployment(ds)
    inits = init_containers(dep)
    assert inits, "expected an initContainer for JWKS substitution"
    env = {e["name"]: e for c in inits for e in c.get("env", [])}
    ref = env["GATEWAY_JWT_SECRET"]["valueFrom"]["secretKeyRef"]
    assert ref["name"] == "ai-proxy-gateway-secrets" and ref["key"] == "jwt-secret", ref
    # init writes into a volume the envoy container also mounts (shared emptyDir).
    init_mounts = {m["mountPath"] for c in inits for m in c.get("volumeMounts", [])}
    main_mounts = {m["mountPath"] for m in containers(dep)[0].get("volumeMounts", [])}
    assert init_mounts & main_mounts, "init + envoy containers must share a generated-config volume"
    # design-for-failure: busybox `base64` wraps at 76 cols; a >57-byte JWT secret would gain a
    # newline and break the single-line JWKS JSON. The pipeline MUST strip newlines (tr -d '\n').
    cmd = "\n".join(
        part
        for c in inits
        for part in c.get("command", [])
        if "base64" in part
    )
    assert "base64" in cmd and "tr -d '\\n'" in cmd, "base64url pipeline must strip newlines for long secrets"


# ── M5 (TLS + Service) ───────────────────────────────────────────────────────────
def test_tls_from_secret():
    ds = docs(template())
    dep = envoy_deployment(ds)
    vols = dep["spec"]["template"]["spec"]["volumes"]
    tls_vol = [v for v in vols if v.get("secret", {}).get("secretName") == "ai-proxy-edge-tls"]
    assert tls_vol, "TLS Secret must be mounted as a volume"
    mounts = [m for m in containers(dep)[0]["volumeMounts"] if m["mountPath"] == "/etc/envoy/certs"]
    assert mounts, "TLS certs must mount at /etc/envoy/certs"
    ports = [p["port"] for p in one(ds, "Service", ENVOY)["spec"]["ports"]]
    assert 8443 in ports and 8080 in ports, ports


def test_service_type_values_driven():
    lb = one(docs(template(set_args={"envoy.service.type": "LoadBalancer"})), "Service", ENVOY)
    assert lb["spec"]["type"] == "LoadBalancer"
    default = one(docs(template()), "Service", ENVOY)
    assert default["spec"]["type"] == "ClusterIP"
    assert {8443, 8080} <= {p["port"] for p in default["spec"]["ports"]}


# ── M6 ──────────────────────────────────────────────────────────────────────────
def test_design_for_failure():
    ds = docs(template())
    dep = envoy_deployment(ds)
    c = containers(dep)[0]
    for probe in ("readinessProbe", "livenessProbe"):
        http = c[probe]["httpGet"]
        # probes hit the dedicated health listener (:9902), NOT the localhost-bound admin (:9901)
        assert http["path"] == "/ready" and http["port"] == 9902, (probe, http)
    res = c["resources"]
    for tier in ("requests", "limits"):
        assert res[tier]["cpu"] and res[tier]["memory"], tier
    assert by_kind(ds, "PodDisruptionBudget", ENVOY), "expected an Envoy PDB"


# ── M7 ──────────────────────────────────────────────────────────────────────────
def test_external_ready_frozen():
    ds = docs(template(set_args={"envoy.enabled": "false"}))
    assert not by_kind(ds, "Deployment", ENVOY)
    assert not by_kind(ds, "Service", ENVOY)
    assert not by_kind(ds, "ConfigMap", ENVOY)
    # Gateway + datastores untouched.
    assert by_kind(ds, "Deployment", GATEWAY_SVC), "gateway must be unaffected"
    assert by_kind(ds, "StatefulSet", "ai-proxy-postgres"), "datastores must be unaffected"


# ── secret_in_configmap_forbidden ────────────────────────────────────────────────
def test_secret_not_in_configmap():
    ds = docs(template(set_args={"gateway.jwtSecret.existingSecret": "some-secret"}))
    _, raw = envoy_config(ds)
    # Only the placeholder — never a resolved/base64url secret value, and no inline `k` value
    # other than the placeholder token.
    assert "GATEWAY_JWT_SECRET_BASE64URL" in raw
    assert '"k":"some-secret"' not in raw and "some-secret" not in raw


# ── tls_literal_forbidden ─────────────────────────────────────────────────────────
def test_no_tls_literal():
    v = VALUES.read_text()
    full = template().stdout
    for marker in ("BEGIN CERTIFICATE", "BEGIN PRIVATE KEY", "BEGIN RSA"):
        assert marker not in v, f"values.yaml must ship no cert material: {marker}"
        assert marker not in full, f"rendered chart must ship no cert material: {marker}"
    vy = yaml.safe_load(v)
    assert "existingSecret" in vy["envoy"]["tls"], "TLS must be a Secret ref"


# ── chart_invalid ─────────────────────────────────────────────────────────────────
def test_chart_invalid_fails():
    broken = template(set_args={
        "envoy.tls.existingSecret": "",
        "gateway.env.environment": "production",
    })
    assert broken.returncode != 0, "blank TLS secret ref in production must fail"
    assert "tls_secret_ref_missing" in (broken.stderr + broken.stdout)
    assert template().returncode == 0, "default render must still succeed"
    assert _helm("lint", str(CHART)).returncode == 0


# ── upstream_host_mismatch ────────────────────────────────────────────────────────
def test_upstream_host_match():
    cfg, _ = envoy_config(docs(template()))
    ep = gateway_endpoint(cfg)
    assert ep["address"] == GATEWAY_FQDN, f"upstream must be the gateway Service FQDN, not {ep['address']!r}"
    assert ep["address"] != "gateway", "must not dial the literal compose host"
    assert ep["address"].startswith(GATEWAY_SVC + "."), "FQDN must be built from the gateway Service name"


# ── admin_port_network_restricted (M6 hardening — gate-driven v2) ─────────────────
def test_admin_port_network_restricted():
    ds = docs(template())
    np = one(ds, "NetworkPolicy", ENVOY)
    assert "Ingress" in np["spec"]["policyTypes"], np["spec"]["policyTypes"]
    # selects the Envoy pods
    sel = np["spec"]["podSelector"]["matchLabels"]
    assert sel.get("app.kubernetes.io/component") == "envoy", sel
    # ingress allows the data plane (8443/8080) but NEVER the admin port 9901
    allowed = {p["port"] for rule in np["spec"]["ingress"] for p in rule.get("ports", [])}
    assert {8443, 8080} <= allowed, allowed
    assert 9901 not in allowed, "admin :9901 must NOT be an allowed ingress port"
    # toggle off → no NetworkPolicy
    off = docs(template(set_args={"envoy.networkPolicy.enabled": "false"}))
    assert not by_kind(off, "NetworkPolicy", ENVOY), "networkPolicy.enabled=false renders none"


# ── admin_localhost_only (M6 hardening — gate-driven v3) ──────────────────────────
def test_admin_localhost_only():
    ds = docs(template())
    cfg, _ = envoy_config(ds)
    # admin bound to loopback only — off-pod config_dump unreachable regardless of CNI/NP
    admin = cfg["admin"]["address"]["socket_address"]
    assert admin["address"] == "127.0.0.1", admin
    assert admin["port_value"] == 9901, admin
    # a dedicated health listener serves probes on :9902 with a health_check filter (no admin surface)
    health = [l for l in cfg["static_resources"]["listeners"]
              if l["address"]["socket_address"]["port_value"] == 9902]
    assert health, "expected a dedicated 0.0.0.0:9902 health listener"
    hl = health[0]
    assert hl["address"]["socket_address"]["address"] == "0.0.0.0", hl["address"]
    hcm = hl["filter_chains"][0]["filters"][0]["typed_config"]
    fnames = [f["name"] for f in hcm["http_filters"]]
    assert "envoy.filters.http.health_check" in fnames, fnames
    # the health listener must NOT carry the auth/admin filters
    assert "envoy.filters.http.ext_authz" not in fnames and "envoy.filters.http.jwt_authn" not in fnames
    # probes target the health port, never the admin port
    c = containers(envoy_deployment(ds))[0]
    assert c["readinessProbe"]["httpGet"]["port"] == 9902
    assert c["livenessProbe"]["httpGet"]["port"] == 9902


# ── edge_incomplete ───────────────────────────────────────────────────────────────
def test_edge_complete():
    ds = docs(template())
    assert containers(envoy_deployment(ds))[0].get("readinessProbe"), "Envoy needs a readiness probe"
    cfg, _ = envoy_config(ds)
    assert tls_hcm(cfg)["upgrade_configs"][0]["upgrade_type"] == "websocket", "WS upgrade required"
