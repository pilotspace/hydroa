/**
 * POST /api/auth/logout
 *
 * BFF route handler — clears the ai_proxy_session cookie.
 * Idempotent: succeeds whether or not the cookie was present.
 */

import { NextRequest, NextResponse } from "next/server";

function buildClearCookieValue(): string {
  const secure = process.env.NODE_ENV !== "development" ? "; Secure" : "";
  return `ai_proxy_session=; HttpOnly${secure}; SameSite=Strict; Path=/; Max-Age=0`;
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  void req; // parameter required by Next.js route handler signature
  const res = NextResponse.json({ ok: true }, { status: 200 });
  res.headers.set("Set-Cookie", buildClearCookieValue());
  return res;
}
