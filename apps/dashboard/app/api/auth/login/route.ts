/**
 * POST /api/auth/login
 *
 * BFF route handler — receives {email, password}, proxies to GATEWAY_URL
 * server-side, sets httpOnly cookie ai_proxy_session on success.
 * Never exposes the JWT in the response body.
 */

import { NextRequest, NextResponse } from "next/server";

function gatewayUrl(): string {
  return (
    process.env.GATEWAY_URL ??
    process.env.NEXT_PUBLIC_GATEWAY_URL ??
    "http://localhost:8080"
  );
}

function buildSessionCookieValue(jwt: string): string {
  const secure = process.env.NODE_ENV !== "development" ? "; Secure" : "";
  return `ai_proxy_session=${jwt}; HttpOnly${secure}; SameSite=Strict; Path=/; Max-Age=86400`;
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json(
      { code: "ERR_BFF_PAYLOAD_INVALID" },
      { status: 400 }
    );
  }

  const email = body.email;
  const password = body.password;

  if (!email || !password || typeof email !== "string" || typeof password !== "string") {
    return NextResponse.json(
      { code: "ERR_BFF_PAYLOAD_INVALID" },
      { status: 400 }
    );
  }

  const upstream = await fetch(`${gatewayUrl()}/admin/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  if (!upstream.ok) {
    let errorBody: unknown;
    try {
      errorBody = await upstream.json();
    } catch {
      errorBody = { title: "Upstream error", status: upstream.status };
    }
    return NextResponse.json(errorBody, { status: upstream.status });
  }

  const data = (await upstream.json()) as { access_token: string };
  const jwt = data.access_token;

  const res = NextResponse.json({ ok: true }, { status: 200 });
  res.headers.set("Set-Cookie", buildSessionCookieValue(jwt));
  return res;
}
