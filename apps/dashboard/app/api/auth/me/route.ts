/**
 * GET /api/auth/me
 *
 * BFF route handler — a thin RELAY to the gateway's authoritative session
 * verifier. It reads the ai_proxy_session cookie and forwards its value as
 * `Authorization: Bearer <token>` to GET {GATEWAY_URL}/admin/auth/me, which
 * cryptographically verifies the JWT (HS256 signature + issuer + required
 * claims + expiry) and returns the identity.
 *
 * SECURITY (v18 auth-me-session-verify): the dashboard NEVER trusts an
 * unverified cookie payload and holds NO signing secret — verification is the
 * gateway's job. The route is FAIL-CLOSED: on any error path it returns an
 * error code and ZERO identity claims. The raw JWT is never echoed or logged.
 *
 * Designed for failure (CLAUDE.md IO rule): the upstream hop is bounded by an
 * AbortSignal timeout and fails CLOSED (network / timeout / 5xx → 503, never a
 * trusted identity). Fail-fast, no retry: the client query is retry:false and a
 * transient blip recovers on the next staleTime refetch — a retry storm on an
 * identity check is worse than a momentary fall back to the base nav.
 */

import { NextRequest, NextResponse } from "next/server";

/** Upstream verify timeout (ms) — a hung gateway must never hang the render path. */
const UPSTREAM_TIMEOUT_MS = 5000;

interface MeResponse {
  user_id?: string | null;
  tenant_id?: string | null;
  email?: string | null;
  role?: string | null;
  /** domain-claims-console M6 (additive): the caller's own tenant display name. */
  tenant_name?: string | null;
}

function gatewayUrl(): string {
  // Server-side only: read the non-public GATEWAY_URL. A NEXT_PUBLIC_-prefixed var would be
  // inlined into the client bundle, leaking the in-cluster gateway address to browsers.
  return process.env.GATEWAY_URL ?? "http://localhost:8080";
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const cookieHeader = req.headers.get("cookie") ?? "";
  const match = cookieHeader.match(/ai_proxy_session=([^;]+)/);
  // Trim so a whitespace-only value collapses to "absent" (a no-session 401),
  // never a malformed `Bearer  <ws>` forwarded upstream.
  const token = match?.[1]?.trim() || undefined;

  // No token → never touch the gateway.
  if (!token) {
    return NextResponse.json({ code: "ERR_AUTH_NO_SESSION" }, { status: 401 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${gatewayUrl()}/admin/auth/me`, {
      method: "GET",
      headers: { Authorization: `Bearer ${token}` },
      // Do NOT follow redirects: a gateway/infra 3xx must never chain to a 200
      // from an unexpected origin that the BFF would relay as a trusted identity.
      // A 3xx then has upstream.ok === false → mapped to a fail-closed 503 below.
      redirect: "manual",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    // Network error / timeout / DNS — fail CLOSED, never a trusted identity.
    return NextResponse.json({ code: "ERR_AUTH_UPSTREAM" }, { status: 503 });
  }

  // The gateway rejected the token (bad signature / expired / tampered / claims).
  if (upstream.status === 401) {
    return NextResponse.json({ code: "ERR_AUTH_INVALID_SESSION" }, { status: 401 });
  }

  // Any other non-200 (5xx, 4xx-other) is an upstream problem — fail CLOSED.
  if (!upstream.ok) {
    return NextResponse.json({ code: "ERR_AUTH_UPSTREAM" }, { status: 503 });
  }

  let claims: MeResponse;
  try {
    claims = (await upstream.json()) as MeResponse;
  } catch {
    return NextResponse.json({ code: "ERR_AUTH_UPSTREAM" }, { status: 503 });
  }

  // Map the verified identity to the stable CurrentUser shape. exp is intentionally
  // null: the gateway already enforces expiry, so the numeric value is dead data and
  // no consumer reads it; the field is kept so the JSON shape stays byte-stable.
  //
  // tenant_name (domain-claims-console M6, additive): RELAYED only when the verified
  // gateway response carries it — the BFF never fabricates an identity/claim field the
  // authoritative verifier did not return (the v18 trust-boundary rule), and an older
  // gateway without the field keeps this route's JSON shape byte-identical. Consumers
  // (useCurrentUser) treat an absent name as "unknown" and fall back to generic copy.
  return NextResponse.json({
    user_id: claims.user_id ?? null,
    tenant_id: claims.tenant_id ?? null,
    email: claims.email ?? null,
    role: claims.role ?? null,
    exp: null,
    ...(typeof claims.tenant_name === "string" ? { tenant_name: claims.tenant_name } : {}),
  });
}
