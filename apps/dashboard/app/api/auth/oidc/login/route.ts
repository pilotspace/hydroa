/**
 * GET /api/auth/oidc/login
 *
 * Pre-auth BFF relay for SSO login initiation. Forwards the gateway's
 * GET /auth/oidc/login 302 + Set-Cookie headers VERBATIM to the browser so the
 * user is carried to the IdP authorize endpoint.
 *
 * Why a dedicated route (not the /api/gw/* catch-all): the catch-all returns
 * 401 ERR_AUTH_NO_SESSION before calling upstream when there is no session
 * cookie, but SSO login is PRE-auth (no session yet). This route performs NO
 * auth check — by design.
 *
 * Security:
 *   - The Location + cookies are sourced ONLY from the trusted gateway response,
 *     never from caller input (the gateway computes the IdP URL from server
 *     config) → no caller-controlled redirect target.
 *   - Only the documented `domain` query param is forwarded; every other param
 *     is dropped (no param smuggling).
 *   - The three forwarded cookies (oidc_state/oidc_nonce/oidc_tenant_id) are
 *     random CSRF tokens + a config selector — no secret/JWT is involved.
 *
 * Design for failure: the upstream fetch is bounded by a timeout; a gateway
 * failure (unreachable OR a 5xx/unexpected response) returns a sanitized 502
 * with NO upstream body (no hang, no secret) so the login page stays usable.
 * Only redirect-family (3xx) responses are forwarded with their Location +
 * cookies, and only 4xx responses (e.g. 404 ERR_OIDC_NOT_CONFIGURED) are
 * relayed verbatim — both are caller-actionable and gateway-authored.
 */

import { NextRequest, NextResponse } from "next/server";
import { sanitizeDomain } from "@/lib/bff-validation";

function gatewayUrl(): string {
  return (
    process.env.GATEWAY_URL ??
    process.env.NEXT_PUBLIC_GATEWAY_URL ??
    "http://localhost:8080"
  );
}

const UPSTREAM_TIMEOUT_MS = 5000;

export async function GET(req: NextRequest): Promise<NextResponse> {
  // Forward ONLY the documented `domain` param — drop everything else so no
  // caller can smuggle a redirect target or other input to the gateway. The
  // value is also bounded/charset-checked (sanitizeDomain); a bad value is
  // dropped (treated as absent) so the relay stays fail-safe.
  const domain = sanitizeDomain(new URL(req.url).searchParams.get("domain"));
  const upstreamUrl =
    `${gatewayUrl()}/auth/oidc/login` +
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

  // Client errors (4xx, e.g. 404 ERR_OIDC_NOT_CONFIGURED) are caller-actionable
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
