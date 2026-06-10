/**
 * middleware.ts — cookie-presence route guard
 *
 * Intercepts requests to /keys and /usage (and any sub-paths).
 * If the ai_proxy_session cookie is absent, redirects to /login with 307.
 * If present, passes through (NextResponse.next()).
 *
 * NOTE: this is a UX guard only — it checks cookie presence, not validity.
 * The gateway validates the JWT on every proxied call and returns 401 if the
 * token is expired or invalid (the BFF proxy then clears the cookie).
 */

import { NextRequest, NextResponse } from "next/server";

export function middleware(req: NextRequest): NextResponse {
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
