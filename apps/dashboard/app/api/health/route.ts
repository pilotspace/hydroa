/**
 * /api/health — liveness/readiness/startup probe target for the k8s deployment.
 *
 * Intentionally dependency-free: no auth, no session check, no gateway/DB call, no SSR.
 * It answers 200 the moment the Next standalone server is accepting connections — which
 * is exactly what a kubelet probe needs. Heavier readiness (gateway reachability) is
 * the gateway's own concern; coupling the dashboard's liveness to it would cascade
 * restarts. `force-dynamic` so the route is never statically optimized to a cached 200.
 */

export const dynamic = "force-dynamic";

export function GET(): Response {
  return new Response(JSON.stringify({ status: "ok" }), {
    status: 200,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}
