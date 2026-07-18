/**
 * lib/public-api-base-url.ts — activation-quickstart §3 v1 #5.
 *
 * The tenant-facing edge origin shown in quickstart materials for a client's OWN
 * SDK/curl usage against Hydroa. Distinct from GATEWAY_URL (non-`NEXT_PUBLIC_`,
 * server-side-only, the BFF's in-cluster upstream address) — this value is
 * deliberately public because it is the SAME origin every external tenant client
 * must already know to call the API directly.
 *
 * NEVER reads GATEWAY_URL. NEVER named/aliased after the public-prefixed gateway
 * variable a prior task already removed as a security fix (it leaked the
 * in-cluster gateway address; see tests/helm/test_dashboard_chart.py
 * test_bff_no_public_gateway_var). This is a client-readable helper, not a BFF
 * resolver, but the name is kept structurally distinct on purpose.
 */
export function publicApiBaseUrl(): string | null {
  const value = process.env.NEXT_PUBLIC_API_BASE_URL;
  return typeof value === "string" && value.length > 0 ? value : null;
}
