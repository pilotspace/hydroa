/**
 * proxy.ts — cookie-presence route guard (Next.js 16)
 *
 * Renamed from middleware.ts in the Next 16 upgrade: Next 16 deprecates the
 * `middleware.ts` filename in favour of `proxy.ts` (export `proxy`). Behavior is
 * BYTE-IDENTICAL to the v13 guard.
 *
 * Intercepts requests to /keys and /usage (and any sub-paths).
 * If the ai_proxy_session cookie is absent, redirects to /login with 307.
 * If present, passes through (NextResponse.next()).
 *
 * NOTE: this is a UX guard only — it checks cookie presence, not validity.
 * The gateway validates the JWT on every proxied call and returns 401 if the
 * token is expired or invalid (the BFF proxy then clears the cookie).
 *
 * Runtime: `proxy.ts` runs on the Node.js runtime (vs middleware's Edge default).
 * Acceptable here — the guard uses no Edge-only API and the app is self-hosted
 * behind Envoy.
 */

import { NextRequest, NextResponse } from "next/server";

export function proxy(req: NextRequest): NextResponse {
  const cookieHeader = req.headers.get("cookie") ?? "";
  const hasSession = /ai_proxy_session=/.test(cookieHeader);

  if (!hasSession) {
    return NextResponse.redirect(new URL("/login", req.url), { status: 307 });
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/keys", "/keys/:path*", "/usage", "/usage/:path*"],
};
