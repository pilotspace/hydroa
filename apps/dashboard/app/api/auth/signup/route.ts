/**
 * POST /api/auth/signup
 *
 * BFF route handler — receives {tenant_name, email, password}, proxies signup
 * then login to GATEWAY_URL, sets httpOnly cookie ai_proxy_session.
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

  const { tenant_name, email, password } = body;

  if (
    !tenant_name ||
    !email ||
    !password ||
    typeof tenant_name !== "string" ||
    typeof email !== "string" ||
    typeof password !== "string"
  ) {
    return NextResponse.json(
      { code: "ERR_BFF_PAYLOAD_INVALID" },
      { status: 400 }
    );
  }

  // Step 1: signup
  const signupRes = await fetch(`${gatewayUrl()}/admin/auth/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tenant_name, email, password }),
  });

  if (!signupRes.ok) {
    let errorBody: unknown;
    try {
      errorBody = await signupRes.json();
    } catch {
      errorBody = { title: "Upstream error", status: signupRes.status };
    }
    return NextResponse.json(errorBody, { status: signupRes.status });
  }

  // Step 2: auto-login to get the JWT
  const loginRes = await fetch(`${gatewayUrl()}/admin/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  if (!loginRes.ok) {
    let errorBody: unknown;
    try {
      errorBody = await loginRes.json();
    } catch {
      errorBody = { title: "Login after signup failed", status: loginRes.status };
    }
    return NextResponse.json(errorBody, { status: loginRes.status });
  }

  const data = (await loginRes.json()) as { access_token: string };
  const jwt = data.access_token;

  const res = NextResponse.json({ ok: true }, { status: 201 });
  res.headers.set("Set-Cookie", buildSessionCookieValue(jwt));
  return res;
}
