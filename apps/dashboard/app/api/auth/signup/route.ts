/**
 * POST /api/auth/signup
 *
 * BFF route handler — receives {tenant_name, email, password}, proxies signup
 * then login to GATEWAY_URL, sets httpOnly cookie ai_proxy_session.
 * Never exposes the JWT in the response body.
 */

import { NextRequest, NextResponse } from "next/server";
import { signupSchema, parseJsonBody } from "@/lib/bff-validation";

function gatewayUrl(): string {
  // Server-side only: read the non-public GATEWAY_URL. A NEXT_PUBLIC_-prefixed var would be
  // inlined into the client bundle, leaking the in-cluster gateway address to browsers.
  return process.env.GATEWAY_URL ?? "http://localhost:8080";
}

function buildSessionCookieValue(jwt: string): string {
  const secure = process.env.NODE_ENV !== "development" ? "; Secure" : "";
  return `ai_proxy_session=${jwt}; HttpOnly${secure}; SameSite=Strict; Path=/; Max-Age=86400`;
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const parsed = await parseJsonBody(req, signupSchema);
  if (!parsed.ok) return parsed.response;
  const { tenant_name, email, password } = parsed.data;

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
