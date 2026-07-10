"""Red suite: local/e2e Envoy dev-stack carve-out parity with the K8s chart (B2 TASK.md §2/§3, M7).

RED until infra/envoy/envoy.yaml and infra/envoy/envoy-prod.yaml gain a
{ match: { prefix: "/v1/realtime/" } } route (ext_authz disabled) placed BEFORE the
general /v1/ rule, byte-mirroring charts/ai-proxy/templates/envoy-configmap.yaml:126-138.

No live Envoy runs in CI (same honest gap realtime-relay-endpoint's own Envoy edit
already flagged for itself) — this is a static YAML-shape pin, not a live-routing test.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ENVOY_YAML = _REPO_ROOT / "infra" / "envoy" / "envoy.yaml"
_ENVOY_PROD_YAML = _REPO_ROOT / "infra" / "envoy" / "envoy-prod.yaml"


def _first_route_config(doc: dict) -> list[dict]:
    """Return the `routes` list off the first HTTP connection manager's route_config."""
    listeners = doc["static_resources"]["listeners"]
    for listener in listeners:
        for chain in listener.get("filter_chains", []):
            for f in chain.get("filters", []):
                typed_config = f.get("typed_config", {})
                route_config = typed_config.get("route_config")
                if route_config is None:
                    continue
                for vhost in route_config.get("virtual_hosts", []):
                    if "routes" in vhost:
                        return vhost["routes"]
    raise AssertionError("no route_config.virtual_hosts[].routes found in the rendered YAML")


def _prefix_of(route: dict) -> str | None:
    return route.get("match", {}).get("prefix")


def _ext_authz_disabled(route: dict) -> bool:
    cfg = (
        route.get("typed_per_filter_config", {})
        .get("envoy.filters.http.ext_authz", {})
    )
    return cfg.get("disabled") is True


def test_envoy_yaml_has_realtime_carveout_before_general_v1() -> None:
    doc = yaml.safe_load(_ENVOY_YAML.read_text())
    routes = _first_route_config(doc)
    prefixes = [_prefix_of(r) for r in routes]

    assert "/v1/realtime/" in prefixes, "envoy.yaml is missing the /v1/realtime/ carve-out route"
    assert "/v1/" in prefixes
    idx_realtime = prefixes.index("/v1/realtime/")
    idx_general = prefixes.index("/v1/")
    assert idx_realtime < idx_general, (
        "the /v1/realtime/ carve-out must be listed BEFORE the general /v1/ rule "
        "(first-match-wins) or it is dead code"
    )

    realtime_route = routes[idx_realtime]
    assert _ext_authz_disabled(realtime_route), (
        "the /v1/realtime/ route must disable ext_authz — a browser WS handshake cannot "
        "carry an Authorization header, so ext_authz would 401 the upgrade"
    )
    assert realtime_route["route"]["cluster"] == "gateway_cluster"


def test_envoy_prod_yaml_has_realtime_carveout_before_general_v1() -> None:
    doc = yaml.safe_load(_ENVOY_PROD_YAML.read_text())
    routes = _first_route_config(doc)
    prefixes = [_prefix_of(r) for r in routes]

    assert "/v1/realtime/" in prefixes, (
        "envoy-prod.yaml is missing the /v1/realtime/ carve-out route"
    )
    assert "/v1/" in prefixes
    idx_realtime = prefixes.index("/v1/realtime/")
    idx_general = prefixes.index("/v1/")
    assert idx_realtime < idx_general

    realtime_route = routes[idx_realtime]
    assert _ext_authz_disabled(realtime_route)
    assert realtime_route["route"]["cluster"] == "gateway_cluster"


def test_dev_stack_carveout_shape_matches_the_shipped_k8s_chart_route() -> None:
    """Byte-shape parity: same match/route/typed_per_filter_config keys as the ALREADY-
    SHIPPED charts/ai-proxy/templates/envoy-configmap.yaml block (Helm templating aside)."""
    doc = yaml.safe_load(_ENVOY_YAML.read_text())
    routes = _first_route_config(doc)
    realtime_route = next(r for r in routes if _prefix_of(r) == "/v1/realtime/")

    assert set(realtime_route.keys()) >= {"match", "route", "typed_per_filter_config"}
    ext_authz_cfg = realtime_route["typed_per_filter_config"]["envoy.filters.http.ext_authz"]
    assert (
        ext_authz_cfg["@type"]
        == "type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute"
    )
    assert ext_authz_cfg["disabled"] is True
