/**
 * POST /auth/saml/callback
 *
 * Pre-auth BFF relay for the SAML CALLBACK half (domain-auto-assign-login M6) —
 * the NET-NEW SAML sibling of app/auth/oidc/callback/route.ts, mirroring its
 * structure line-for-line. It receives the IdP's HTTP-POST-binding form
 * submission and forwards the SAMLResponse (+ RelayState) to the gateway's
 * POST /auth/saml/acs: the gateway validates signature/audience/replay via its
 * server-side pending-request store, mints the `ai_proxy_session` JWT, and
 * returns a 302 (+ ?joined=1 on a first login) + Set-Cookie. This relay only
 * forwards that trusted gateway response to the browser — it never sees a token
 * or secret.
 *
 * Unlike the OIDC relay, NO cookies are forwarded upstream: SAML tenant
 * identity is resolved EXCLUSIVELY via the gateway's server-side pending-
 * request store, never a cookie. This route performs NO auth check — the
 * callback is pre-session by design.
 *
 * Security (carried inviolables — a violation is a HARD-STOP):
 *   - Assertion validation stays GATEWAY-side; the relay never parses XML.
 *   - NO token/JWT/secret appears in any response BODY — the session is carried
 *     ONLY by the forwarded httpOnly `Set-Cookie`. Every relay response has an
 *     empty body.
 *   - Only the documented SAMLResponse/RelayState form fields are forwarded;
 *     every other field is dropped (no smuggling).
 *
 * Design for failure (CLAUDE.md): the upstream fetch is bounded by a timeout and
 * never auto-follows the gateway 302. Any gateway error (4xx/5xx/unexpected) or
 * an unreachable gateway fails CLOSED — the browser is bounced to
 * `/login?sso_error=<sanitized hint>` with no upstream body, no hang, no secret.
 */

import { NextRequest, NextResponse } from "next/server";

function gatewayUrl(): string {
  // Server-side only: read the non-public GATEWAY_URL. A NEXT_PUBLIC_-prefixed var would be
  // inlined into the client bundle, leaking the in-cluster gateway address to browsers.
  return process.env.GATEWAY_URL ?? "http://localhost:8080";
}

const UPSTREAM_TIMEOUT_MS = 5000;

// A public error hint must be a bare enum token (e.g. ERR_SAML_SIGNATURE_INVALID) —
// never free text. Anything else is collapsed to a generic hint so no upstream
// string can be smuggled into the redirect URL or the login UI.
const SAFE_CODE = /^[A-Za-z0-9_]+$/;

/** 302 to the login page with a sanitized, non-leaking error hint. */
function bounceToLogin(hint: string): NextResponse {
  // Bounded length + bare-enum charset: a rogue/compromised gateway cannot smuggle
  // an oversized or crafted string into the Location header or the login UI.
  const safe = hint.length <= 64 && SAFE_CODE.test(hint) ? hint : "failed";
  const res = new NextResponse(null, { status: 302 });
  res.headers.set("location", `/login?sso_error=${safe}`);
  return res;
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  // Forward only `SAMLResponse` + `RelayState`; drop every other form field
  // (no smuggling). No cookies are forwarded — the gateway resolves the tenant
  // via its server-side pending-request store.
  let form: FormData;
  try {
    form = await req.formData();
  } catch {
    return bounceToLogin("failed");
  }
  const samlResponse = form.get("SAMLResponse");
  const relayState = form.get("RelayState");
  const body = new URLSearchParams();
  if (typeof samlResponse === "string") body.set("SAMLResponse", samlResponse);
  if (typeof relayState === "string") body.set("RelayState", relayState);
  const upstreamUrl = `${gatewayUrl()}/auth/saml/acs`;

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: body.toString(),
      // Capture the gateway 302 instead of auto-following it.
      redirect: "manual",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    // Gateway unreachable / timed out — fail closed (no hang, no secret).
    return bounceToLogin("upstream");
  }

  // Happy path: forward the gateway 3xx with its Location (incl. any ?joined=1)
  // + every Set-Cookie verbatim. The body stays empty so no token can leak
  // through it.
  if (upstream.status >= 300 && upstream.status < 400) {
    const res = new NextResponse(null, { status: upstream.status });
    const location = upstream.headers.get("location");
    if (location) res.headers.set("location", location);
    for (const cookie of upstream.headers.getSetCookie()) {
      res.headers.append("set-cookie", cookie);
    }
    return res;
  }

  // Gateway client error (4xx, e.g. ERR_SAML_SIGNATURE_INVALID) — bounce to
  // login with the sanitized problem.code as a public hint. The upstream body
  // is never relayed to the browser.
  if (upstream.status >= 400 && upstream.status < 500) {
    let hint = "failed";
    const contentType = upstream.headers.get("content-type") ?? "";
    if (contentType.includes("application/json")) {
      const problem = (await upstream.json().catch(() => null)) as {
        code?: unknown;
      } | null;
      if (problem && typeof problem.code === "string") hint = problem.code;
    }
    return bounceToLogin(hint);
  }

  // 5xx or an unexpected status (1xx/2xx) — never relay the upstream body to this
  // unauthenticated caller. Bounce to login with a generic hint.
  return bounceToLogin("upstream");
}
