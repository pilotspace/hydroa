/**
 * GET /auth/oidc/callback
 *
 * Pre-auth BFF relay for the SSO CALLBACK half (the v15 `oidc-login-relay`
 * shipped the login-redirect half). It carries the browser through the
 * gateway's code→session exchange: the gateway validates state/nonce, performs
 * the server-side token exchange (its own client_secret), mints the
 * `ai_proxy_session` JWT, and returns a 302 + Set-Cookie. This relay only
 * forwards that trusted gateway response to the browser — it never sees a token
 * or secret.
 *
 * Why the `/auth/oidc/...` path (not `/api/auth/...`): the gateway sets the
 * three handshake cookies at `Path=/auth/oidc`, so they are only sent by the
 * browser to a request path under `/auth/oidc`. Living here lets them reach the
 * relay WITHOUT rewriting the frozen v15 login relay. This route performs NO
 * auth check — the callback is pre-session by design.
 *
 * Security (carried inviolables — a violation is a HARD-STOP):
 *   - The token exchange + client_secret stay GATEWAY-side; the relay never
 *     touches either.
 *   - NO token/JWT/secret appears in any response BODY — the session is carried
 *     ONLY by the forwarded httpOnly `Set-Cookie`. Every relay response has an
 *     empty body.
 *   - Only the three `oidc_*` handshake cookies are forwarded upstream (minimal
 *     disclosure); the stale `ai_proxy_session` + any other cookie are dropped.
 *   - `code`/`state` are forwarded but never trusted here — the gateway
 *     validates them against the cookie (CSRF/state defense).
 *
 * Design for failure (CLAUDE.md): the upstream fetch is bounded by a timeout and
 * never auto-follows the gateway 302. Any gateway error (4xx/5xx/unexpected) or
 * an unreachable gateway fails CLOSED — the browser is bounced to
 * `/login?sso_error=<sanitized hint>` with no upstream body, no hang, no secret.
 */

import { NextRequest, NextResponse } from "next/server";

function gatewayUrl(): string {
  return (
    process.env.GATEWAY_URL ??
    process.env.NEXT_PUBLIC_GATEWAY_URL ??
    "http://localhost:8080"
  );
}

const UPSTREAM_TIMEOUT_MS = 5000;

// The three OIDC handshake cookies (CSRF state/nonce + tenant selector) — the
// ONLY cookies forwarded upstream. They are gateway-authored, not secrets/JWTs.
const OIDC_COOKIE_NAMES = ["oidc_state", "oidc_nonce", "oidc_tenant_id"] as const;

// A public error hint must be a bare enum token (e.g. ERR_OIDC_STATE_MISMATCH) —
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

export async function GET(req: NextRequest): Promise<NextResponse> {
  // Forward only `code` + `state`; drop every other query param (no smuggling).
  const inUrl = new URL(req.url);
  const code = inUrl.searchParams.get("code");
  const state = inUrl.searchParams.get("state");
  const qs = new URLSearchParams();
  if (code) qs.set("code", code);
  if (state) qs.set("state", state);
  const upstreamUrl = `${gatewayUrl()}/auth/oidc/callback?${qs.toString()}`;

  // Forward ONLY the 3 oidc_* handshake cookies — never the session or others.
  const forwardedCookie = OIDC_COOKIE_NAMES.map((name) => {
    const value = req.cookies.get(name)?.value;
    return value === undefined ? null : `${name}=${value}`;
  })
    .filter((c): c is string => c !== null)
    .join("; ");

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, {
      method: "GET",
      headers: forwardedCookie ? { cookie: forwardedCookie } : {},
      // Capture the gateway 302 instead of auto-following it.
      redirect: "manual",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    // Gateway unreachable / timed out — fail closed (no hang, no secret).
    return bounceToLogin("upstream");
  }

  // Happy path: forward the gateway 3xx with its Location + every Set-Cookie
  // (ai_proxy_session + the cleared oidc_*) verbatim. The body stays empty so no
  // token can leak through it.
  if (upstream.status >= 300 && upstream.status < 400) {
    const res = new NextResponse(null, { status: upstream.status });
    const location = upstream.headers.get("location");
    if (location) res.headers.set("location", location);
    for (const cookie of upstream.headers.getSetCookie()) {
      res.headers.append("set-cookie", cookie);
    }
    return res;
  }

  // Gateway client error (4xx, e.g. ERR_OIDC_STATE_MISMATCH) — bounce to login
  // with the sanitized problem.code as a public hint. The upstream body is never
  // relayed to the browser.
  if (upstream.status >= 400 && upstream.status < 500) {
    let hint = "failed";
    const contentType = upstream.headers.get("content-type") ?? "";
    if (contentType.includes("application/json")) {
      const body = (await upstream.json().catch(() => null)) as {
        code?: unknown;
      } | null;
      if (body && typeof body.code === "string") hint = body.code;
    }
    return bounceToLogin(hint);
  }

  // 5xx or an unexpected status (1xx/2xx) — never relay the upstream body to this
  // unauthenticated caller. Bounce to login with a generic hint.
  return bounceToLogin("upstream");
}
