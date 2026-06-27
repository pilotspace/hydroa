"""Guard the Envoy ext_authz carve-out for the realtime relay (v53 task e2e-platform-features §3 M5).

The edge (`charts/ai-proxy/templates/envoy-configmap.yaml`) DISABLES ext_authz for the prefix
`/v1/realtime/` so the header-less relay WS handshake can upgrade and the gateway's in-band
auth-over-WS becomes the sole authenticator. That carve-out is ONLY safe while every route under
the prefix is a WebSocket route — a WS route is guarded by the in-band auth-over-WS (close 4401 on
a bad/absent token before any work). A *non-WS HTTP* route added under `/v1/realtime/` would be
silently UNAUTHENTICATED at the edge.

These default-suite tests fail loudly at CI (no cluster needed) if that invariant is ever broken,
turning the refute-read's "standing governance gap" (Q2) into an enforced contract. Keep the prefix
in lockstep with the Envoy route match.
"""

from __future__ import annotations

from starlette.routing import WebSocketRoute

from gateway.main import create_app

# MUST match the `- match: { prefix: "/v1/realtime/" }` route in envoy-configmap.yaml.
CARVE_OUT_PREFIX = "/v1/realtime/"


def test_only_websocket_routes_under_realtime_carveout() -> None:
    """Every route under the ext_authz carve-out prefix must be a WebSocket route."""
    app = create_app()
    offenders = [
        getattr(route, "path", repr(route))
        for route in app.routes
        if getattr(route, "path", "").startswith(CARVE_OUT_PREFIX)
        and not isinstance(route, WebSocketRoute)
    ]
    assert not offenders, (
        f"non-WebSocket route(s) registered under the ext_authz carve-out {CARVE_OUT_PREFIX!r}: "
        f"{offenders}. Envoy disables ext_authz for this prefix, so ONLY the gateway's in-band "
        "auth-over-WS guards it — and that exists only for WebSocket endpoints. A non-WS route "
        "here is UNAUTHENTICATED at the edge. Move it out from under /v1/realtime/, or add edge "
        "auth (ext_authz/jwt_authn) for it before registering it."
    )


def test_relay_ws_is_under_the_carveout() -> None:
    """Anchor so the guard above can't pass vacuously: the relay WS IS under the carved-out prefix."""
    app = create_app()
    ws_paths = {
        route.path for route in app.routes if isinstance(route, WebSocketRoute)
    }
    assert "/v1/realtime/relay" in ws_paths, (
        "the relay WS /v1/realtime/relay is not registered — the carve-out guard would be vacuous "
        f"(WebSocket routes found: {sorted(ws_paths)})"
    )
    assert "/v1/realtime/relay".startswith(CARVE_OUT_PREFIX), (
        "the relay path no longer falls under the carve-out prefix — re-check the Envoy route match"
    )
