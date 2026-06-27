"""Red/green SHAPE tests for the kind bootstrap harness (v53 task 6).

Mechanism (Tin 2026-06-27, FROZEN @ v1): `make kind-up` is a reproducible, zero-cloud-creds
harness — preflight tools → ensure a kind cluster (infra/kind/cluster.yaml) → build+`kind load`
both images → `kubectl apply` a TEST-ONLY LLM upstream stub (infra/kind/upstream-stub.yaml) →
`helm upgrade --install -f charts/ai-proxy/values-kind.yaml` → BOUNDED wait until the whole stack
is Ready. The prod chart is unchanged (overlay only; stub is harness-applied, not a chart template).

These tests assert the ARTIFACT SHAPE only — fast, CI-safe, no cluster. The live
`kind-up`→stack-Ready proof is the build/verify evidence, not a pytest here.
Red-for-the-right-reason: before the overlay/manifests/Makefile-targets exist, every assertion fails.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
CHART = REPO / "charts" / "ai-proxy"
OVERLAY = CHART / "values-kind.yaml"
CLUSTER = REPO / "infra" / "kind" / "cluster.yaml"
STUB = REPO / "infra" / "kind" / "upstream-stub.yaml"
MAKEFILE = REPO / "Makefile"
STUB_SVC = "ai-proxy-upstream-stub"

HELM = shutil.which("helm")


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    assert HELM, "helm binary required on PATH (brew install helm) — freeze decision"
    return subprocess.run([HELM, *args], cwd=str(REPO), capture_output=True, text=True)


def render_kind() -> list[dict]:
    proc = _helm("template", "ai-proxy", str(CHART), "-f", str(OVERLAY))
    assert proc.returncode == 0, f"helm template with the kind overlay failed:\n{proc.stderr}"
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def by_kind_name(docs: list[dict], kind: str, name: str) -> dict | None:
    for d in docs:
        if d.get("kind") == kind and d.get("metadata", {}).get("name") == name:
            return d
    return None


def gateway_deploy(docs: list[dict]) -> dict:
    d = by_kind_name(docs, "Deployment", "ai-proxy-gateway")
    assert d, "gateway Deployment must render under the kind overlay"
    return d


def gw_container(dep: dict) -> dict:
    for c in dep["spec"]["template"]["spec"]["containers"]:
        if c["name"] == "gateway":
            return c
    raise AssertionError("gateway container not found")


def env_map(container: dict) -> dict[str, str]:
    out = {}
    for e in container.get("env", []):
        if "value" in e:
            out[e["name"]] = e["value"]
    return out


# ── overlay: local images, pullPolicy Never ───────────────────────────────────────
def test_kind_overlay_renders_local_images():
    docs = render_kind()
    gw = gateway_deploy(docs)
    dash = by_kind_name(docs, "Deployment", "ai-proxy-dashboard")
    assert dash, "dashboard Deployment must render under the kind overlay"
    gw_img = gw_container(gw)["image"]
    dash_img = dash["spec"]["template"]["spec"]["containers"][0]["image"]
    # local, registry-free image refs (no ghcr.io push needed for kind load)
    assert "ghcr.io" not in gw_img, f"kind gateway image must be local, got {gw_img}"
    assert "ghcr.io" not in dash_img, f"kind dashboard image must be local, got {dash_img}"
    # pullPolicy Never so the kubelet uses the kind-loaded image, never a remote pull
    assert gw_container(gw).get("imagePullPolicy") == "Never", "gateway pullPolicy must be Never for kind"
    assert dash["spec"]["template"]["spec"]["containers"][0].get("imagePullPolicy") == "Never", (
        "dashboard pullPolicy must be Never for kind"
    )


# ── overlay: test secrets created + wired ──────────────────────────────────────────
def test_kind_overlay_creates_test_secrets():
    docs = render_kind()
    assert by_kind_name(docs, "Secret", "ai-proxy-datastore-secrets"), (
        "datastores.secrets.create=true must mint ai-proxy-datastore-secrets for kind"
    )
    assert by_kind_name(docs, "Secret", "ai-proxy-gateway-secrets"), (
        "gateway.jwtSecret.createSecret=true must mint ai-proxy-gateway-secrets for kind"
    )
    gw = gateway_deploy(docs)
    db = next((e for e in gw_container(gw).get("env", []) if e["name"] == "GATEWAY_DATABASE_URL"), None)
    assert db and db.get("valueFrom", {}).get("secretKeyRef", {}).get("name") == "ai-proxy-datastore-secrets", (
        "the gateway DSN must source from the created datastore Secret under the kind overlay"
    )


# ── overlay: upstream points at the in-cluster stub ────────────────────────────────
def test_kind_overlay_points_upstream_at_stub():
    docs = render_kind()
    env = env_map(gw_container(gateway_deploy(docs)))
    for var in ("GATEWAY_OPENROUTER_BASE_URL", "GATEWAY_OPENAI_BASE_URL",
                "GATEWAY_ANTHROPIC_BASE_URL", "GATEWAY_GOOGLE_BASE_URL"):
        assert var in env, f"{var} must render"
        assert STUB_SVC in env[var], (
            f"{var} must point at the in-cluster stub Service, got {env[var]}"
        )


# ── overlay-only EXCEPT the authorized chart-template fixes (envoy FQDN/startupProbe + enc-key wiring) ─
def test_kind_overlay_only_authorized_template_edit():
    """kind-bootstrap is overlay-only EXCEPT for a small, explicitly-authorized set of chart-template
    edits. Two were forced by the live kind run: (v2) envoy-configmap.yaml gains FQDN cluster addresses
    (short names never resolve under envoy's c-ares); (v3) envoy-deployment.yaml renders the startupProbe
    values.yaml already defines (without it, liveness kills envoy during cold-start DNS warming). Two more
    were authorized by task-7's v2 change-request (Tin, "Extend task 7"): gateway-deployment.yaml +
    gateway-secret.yaml wire GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY (env-agnostic, mirrors jwtSecret) — the
    BYOK-only gateway 500s every /v1 completion without it, which the task-7 live e2e first surfaced. No
    OTHER template may change."""
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--", "charts/ai-proxy/templates"],
        cwd=str(REPO), capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"git status failed: {proc.stderr}"
    changed = [ln[3:] for ln in proc.stdout.splitlines() if ln.strip()]
    allowed = {
        "charts/ai-proxy/templates/envoy-configmap.yaml",      # v2: FQDN cluster addresses
        "charts/ai-proxy/templates/envoy-deployment.yaml",     # v3: startupProbe wiring
        "charts/ai-proxy/templates/gateway-deployment.yaml",   # task-7 v2: enc-key env (GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY)
        "charts/ai-proxy/templates/gateway-secret.yaml",       # task-7 v2: enc-key stringData (render-safe)
    }
    unexpected = [c for c in changed if c not in allowed]
    assert not unexpected, (
        f"only the authorized envoy (v2/v3) + gateway enc-key (task-7 v2) template edits are allowed; "
        f"unexpected template changes:\n{unexpected}"
    )


# ── stub manifest shape ────────────────────────────────────────────────────────────
def test_upstream_stub_manifest_shape():
    assert STUB.exists(), "infra/kind/upstream-stub.yaml must exist (harness-applied, not a chart template)"
    docs = [d for d in yaml.safe_load_all(STUB.read_text()) if d]
    dep = by_kind_name(docs, "Deployment", STUB_SVC)
    svc = by_kind_name(docs, "Service", STUB_SVC)
    assert dep, f"stub must define a Deployment named {STUB_SVC}"
    assert svc, f"stub must define a Service named {STUB_SVC}"
    pod = dep["spec"]["template"]["spec"]
    psc = pod.get("securityContext", {})
    c0 = pod["containers"][0]
    csc = c0.get("securityContext", {})
    assert psc.get("runAsNonRoot") is True or csc.get("runAsNonRoot") is True, "stub must run non-root"
    assert c0.get("readinessProbe"), "stub container needs a readinessProbe (Ready gate)"
    ports = [p.get("port") for p in svc["spec"]["ports"]]
    assert 8080 in ports, f"stub Service must expose port 8080, got {ports}"


# ── kind cluster config shape ──────────────────────────────────────────────────────
def test_cluster_config_shape():
    assert CLUSTER.exists(), "infra/kind/cluster.yaml must exist"
    cfg = yaml.safe_load(CLUSTER.read_text())
    assert cfg.get("kind") == "Cluster", "must be a kind Cluster config"
    assert cfg.get("apiVersion", "").startswith("kind.x-k8s.io/"), "must use the kind apiVersion"
    nodes = cfg.get("nodes", [])
    mappings = [m for n in nodes for m in n.get("extraPortMappings", [])]
    assert mappings, "cluster must map the edge port to the host (extraPortMappings) for host e2e"


# ── Makefile targets ───────────────────────────────────────────────────────────────
def test_makefile_declares_kind_targets():
    mk = MAKEFILE.read_text()
    for target in ("kind-up:", "kind-down:", "kind-load:", "kind-smoke:"):
        assert target in mk, f"Makefile must define the {target[:-1]} target"
    # listed in .PHONY (targets are not files)
    phony = "\n".join(line for line in mk.splitlines() if ".PHONY" in line or line.strip().startswith("kind-"))
    for name in ("kind-up", "kind-down", "kind-load", "kind-smoke"):
        assert name in mk, f"{name} must appear in the Makefile"
    # kind-up actually drives build + load + helm install
    assert "kind load" in mk, "kind-up must kind-load the built images"
    assert "helm upgrade --install" in mk or "helm install" in mk, "kind-up must helm-install the chart"
    assert "values-kind.yaml" in mk, "kind-up must install with the kind overlay"


# ── Makefile: bounded wait + diagnostics on failure (design-for-failure) ───────────
def test_makefile_wait_is_bounded_and_dumps_diag():
    mk = MAKEFILE.read_text()
    assert "--timeout" in mk, "every wait must be bounded by an explicit --timeout (no unbounded wait)"
    # a diagnostics path exists for the not-ready failure (dump pods/events before exit)
    assert "get pods" in mk or "describe" in mk or "get events" in mk, (
        "kind-up must dump diagnostics on a not-ready timeout (design-for-failure)"
    )


# ── v2 regression: envoy STRICT_DNS clusters must use FQDNs ─────────────────────────
def test_envoy_clusters_are_fqdn():
    """The live kind run proved short k8s service names never resolve under envoy's c-ares
    resolver (it does not apply the resolv.conf search domains), so the edge can never come
    Ready. Every STRICT_DNS cluster address in the envoy ConfigMap must be a `.svc.cluster.local`
    FQDN. Guards the task-3 envoy config against regressing to the broken short-name form."""
    import re

    proc = _helm("template", "ai-proxy", str(CHART))
    assert proc.returncode == 0, f"helm template failed:\n{proc.stderr}"
    docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
    cm = next(
        d for d in docs
        if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-envoy")
    )
    envoy_yaml = next(v for k, v in cm["data"].items() if k.endswith(".yaml"))
    cfg = yaml.safe_load(envoy_yaml)
    clusters = cfg["static_resources"]["clusters"]
    dns_clusters = [c for c in clusters if c.get("type") in ("STRICT_DNS", "LOGICAL_DNS")]
    assert dns_clusters, "expected at least one STRICT_DNS cluster (gateway/dashboard)"
    for c in dns_clusters:
        for ep in c["load_assignment"]["endpoints"]:
            for lb in ep["lb_endpoints"]:
                addr = lb["endpoint"]["address"]["socket_address"]["address"]
                # a bare short name (no dots) does not resolve under envoy's c-ares in k8s
                assert addr.endswith(".svc.cluster.local"), (
                    f"cluster {c['name']} address {addr!r} must be an FQDN "
                    f"(.svc.cluster.local) — short names never resolve in real k8s"
                )
                assert not re.fullmatch(r"[a-z0-9-]+", addr), (
                    f"cluster {c['name']} address {addr!r} is a bare short name"
                )


# ── v3 regression: envoy Deployment must render the startupProbe ───────────────────
def test_envoy_renders_startup_probe():
    """The cold-cluster kind run proved envoy needs a startupProbe: its health listener binds only
    after STRICT_DNS warming completes, and on a cold cluster that exceeds the liveness budget, so
    without a startupProbe the kubelet kills envoy mid-warming → CrashLoopBackOff. values.yaml
    already defines `envoy.probes.startup`; the gateway+dashboard templates render it but the envoy
    template dropped it. Assert the envoy Deployment container declares a startupProbe whose httpGet
    hits /ready on the health port — mirroring the siblings, guarding against the no-probe regress."""
    proc = _helm("template", "ai-proxy", str(CHART))
    assert proc.returncode == 0, f"helm template failed:\n{proc.stderr}"
    docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
    dep = next(
        d for d in docs
        if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-envoy")
    )
    envoy_c = next(
        c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == "envoy"
    )
    sp = envoy_c.get("startupProbe")
    assert sp, "envoy container must declare a startupProbe (suppresses liveness until first /ready 200)"
    hg = sp.get("httpGet", {})
    assert hg.get("path") == "/ready", f"startupProbe must GET /ready, got {hg.get('path')!r}"
    # the probe targets the dedicated health listener, not the loopback admin port
    health_port = 9902
    assert hg.get("port") == health_port, (
        f"startupProbe must target the health port {health_port}, got {hg.get('port')!r}"
    )
    # a real startup budget (not the tight liveness default) — failureThreshold high enough to cover
    # cold-start DNS warming (mirrors the gateway/dashboard 30×5s = 150s tolerance)
    assert sp.get("failureThreshold", 0) >= 10, (
        f"startupProbe failureThreshold must give a real startup budget, got {sp.get('failureThreshold')!r}"
    )


# ── v4 regression: envoy STRICT_DNS clusters must force V4_ONLY DNS resolution ─────
def test_envoy_clusters_v4_only():
    """The cold-kind run proved envoy's default `dns_lookup_family: AUTO` (AAAA/IPv6 lookups) leaves
    the STRICT_DNS clusters host-less on an IPv4-only cluster → /v1 403, / 503. Every STRICT_DNS
    cluster in the envoy ConfigMap must set `dns_lookup_family: V4_ONLY` so envoy resolves the V4
    Service ClusterIPs. Guards against regressing to the default that resolves nothing in kind/k8s."""
    proc = _helm("template", "ai-proxy", str(CHART))
    assert proc.returncode == 0, f"helm template failed:\n{proc.stderr}"
    docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
    cm = next(
        d for d in docs
        if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-envoy")
    )
    envoy_yaml = next(v for k, v in cm["data"].items() if k.endswith(".yaml"))
    cfg = yaml.safe_load(envoy_yaml)
    dns_clusters = [c for c in cfg["static_resources"]["clusters"]
                    if c.get("type") in ("STRICT_DNS", "LOGICAL_DNS")]
    assert dns_clusters, "expected at least one STRICT_DNS cluster (gateway/dashboard)"
    for c in dns_clusters:
        assert c.get("dns_lookup_family") == "V4_ONLY", (
            f"cluster {c['name']} must set dns_lookup_family: V4_ONLY (got "
            f"{c.get('dns_lookup_family')!r}) — the default AUTO resolves nothing on IPv4 k8s"
        )


# ── v4 regression: the kind overlay must run NP-free (kindnet ENFORCES NP here) ────
def test_kind_overlay_disables_networkpolicies():
    """kindnet in this kind version ENFORCES NetworkPolicy (the §1 'kindnet ignores NP' assumption is
    false), and the prod envoy/dashboard NPs block the legit edge→upstream path under enforcement
    (proven: deleting them flips the edge from 503/403 to 200/200/401). The kind overlay must run the
    proof NP-free — `helm template -f values-kind.yaml` emits NO NetworkPolicy. Prod keeps NP (default
    enabled); this only relaxes the kind overlay, consistent with 'NP not validated in kind'."""
    docs = render_kind()
    nps = [d for d in docs if d.get("kind") == "NetworkPolicy"]
    assert not nps, (
        "the kind overlay must disable all NetworkPolicies (kindnet enforces NP and the prod NPs "
        f"block the edge path); got NetworkPolicies: {[d['metadata']['name'] for d in nps]}"
    )


# ── no real secret literal anywhere in the kind harness ────────────────────────────
def test_no_real_secret_literal():
    blobs = []
    for p in (OVERLAY, CLUSTER, STUB):
        if p.exists():
            blobs.append(p.read_text())
    text = "\n".join(blobs)
    for marker in ("BEGIN PRIVATE KEY", "BEGIN CERTIFICATE", "BEGIN RSA"):
        assert marker not in text, f"kind harness must ship no real secret material: {marker}"
