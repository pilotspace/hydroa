/**
 * GET /auth/saml/login
 *
 * Pre-auth BFF relay for SAML login initiation (domain-auto-assign-login M6) —
 * the NET-NEW SAML sibling of app/api/auth/oidc/login/route.ts, mirroring its
 * structure exactly with the upstream path swapped to the gateway's
 * GET /auth/saml/login. Forwards the gateway's 302 (to the IdP, HTTP-Redirect
 * binding) VERBATIM to the browser.
 *
 * Lives under /auth/saml (not /api/auth) so the login + callback halves share
 * one path family; SAML uses NO handshake cookies (the gateway's server-side
 * pending-request store carries the state), so no cookie path constraint
 * applies. This route performs NO auth check — SSO login is PRE-auth by design.
 *
 * Security:
 *   - The Location is sourced ONLY from the trusted gateway response, never
 *     from caller input → no caller-controlled redirect target.
 *   - Only the documented `domain` query param is forwarded; every other param
 *     is dropped (no param smuggling).
 *
 * Design for failure: the upstream fetch is bounded by a timeout; a gateway
 * failure (unreachable OR a 5xx/unexpected response) returns a sanitized 502
 * with NO upstream body (no hang, no secret) so the login page stays usable.
 * Only redirect-family (3xx) responses are forwarded with their Location +
 * cookies, and only 4xx responses (e.g. 404 ERR_SAML_NOT_CONFIGURED) are
 * relayed verbatim — both are caller-actionable and gateway-authored.
 */

import { NextRequest, NextResponse } from "next/server";
import { sanitizeDomain } from "@/lib/bff-validation";

function gatewayUrl(): string {
  // Server-side only: read the non-public GATEWAY_URL. A NEXT_PUBLIC_-prefixed var would be
  // inlined into the client bundle, leaking the in-cluster gateway address to browsers.
  return process.env.GATEWAY_URL ?? "http://localhost:8080";
}

const UPSTREAM_TIMEOUT_MS = 5000;

export async function GET(req: NextRequest): Promise<NextResponse> {
  // Forward ONLY the documented `domain` param — drop everything else so no
  // caller can smuggle a redirect target or other input to the gateway. The
  // value is also bounded/charset-checked (sanitizeDomain); a bad value is
  // dropped (treated as absent) so the relay stays fail-safe.
  const domain = sanitizeDomain(new URL(req.url).searchParams.get("domain"));
  const upstreamUrl =
    `${gatewayUrl()}/auth/saml/login` +
    (domain ? `?domain=${encodeURIComponent(domain)}` : "");

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, {
      method: "GET",
      // Capture the 302 instead of following it to the (external) IdP.
      redirect: "manual",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    // Gateway unreachable / timed out — fail closed, no secret, no hang.
    return NextResponse.json(
      { code: "ERR_BFF_GATEWAY_UNREACHABLE" },
      { status: 502 },
    );
  }

  // Happy path: forward a redirect (3xx) with its Location + every Set-Cookie.
  if (upstream.status >= 300 && upstream.status < 400) {
    const res = new NextResponse(null, { status: upstream.status });
    const location = upstream.headers.get("location");
    if (location) res.headers.set("location", location);
    for (const cookie of upstream.headers.getSetCookie()) {
      res.headers.append("set-cookie", cookie);
    }
    return res;
  }

  // Client errors (4xx, e.g. 404 ERR_SAML_NOT_CONFIGURED) are caller-actionable
  // and gateway-authored — forward status + body verbatim so the page reacts.
  if (upstream.status >= 400 && upstream.status < 500) {
    const contentType = upstream.headers.get("content-type") ?? "";
    if (contentType.includes("application/json")) {
      const body: unknown = await upstream.json().catch(() => ({}));
      return NextResponse.json(body, { status: upstream.status });
    }
    const text = await upstream.text().catch(() => "");
    return new NextResponse(text, {
      status: upstream.status,
      headers: contentType ? { "content-type": contentType } : undefined,
    });
  }

  // Anything else (5xx or an unexpected 1xx/2xx) — do NOT relay the upstream
  // body to this unauthenticated caller. Sanitize to a 502 (defense-in-depth).
  return NextResponse.json({ code: "ERR_BFF_GATEWAY_ERROR" }, { status: 502 });
}
