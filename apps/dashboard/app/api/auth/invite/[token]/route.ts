/**
 * GET|POST /api/auth/invite/[token]
 *
 * PUBLIC (unauthenticated) BFF routes for the invite-accept flow — mirror
 * app/api/auth/signup/route.ts's gatewayUrl()/cookie pattern exactly, but take the
 * invite token as a PATH param (never re-appended as a query string).
 *
 * GET  -> passthrough preview: GET /invites/{token} -> {tenant_name, email, role,
 *         expires_at}. Relays 404/409/410 problem+json verbatim
 *         (ERR_INVITE_NOT_FOUND / ERR_INVITE_NOT_PENDING / ERR_INVITE_EXPIRED) so the
 *         accept page can render a specific, non-500 message.
 * POST -> {password} -> accept -> auto-login -> Set-Cookie ai_proxy_session, the
 *         SAME two-step chain as the signup route. A failure at EITHER step relays
 *         the upstream error verbatim and skips the next step entirely — no
 *         auto-login, no cookie, on a rejected/expired/already-used invite. The JWT
 *         is never exposed in the response body.
 */

import { NextRequest, NextResponse } from "next/server";
import { acceptInviteSchema, parseJsonBody } from "@/lib/bff-validation";

function gatewayUrl(): string {
  // Server-side only: read the non-public GATEWAY_URL. A NEXT_PUBLIC_-prefixed var would be
  // inlined into the client bundle, leaking the in-cluster gateway address to browsers.
  return process.env.GATEWAY_URL ?? "http://localhost:8080";
}

function buildSessionCookieValue(jwt: string): string {
  const secure = process.env.NODE_ENV !== "development" ? "; Secure" : "";
  return `ai_proxy_session=${jwt}; HttpOnly${secure}; SameSite=Strict; Path=/; Max-Age=86400`;
}

// Next.js 15/16 App Router: params is always a Promise. Unit tests pass a plain
// object which is also thenable via Promise.resolve (see tests-bff/route-handlers.test.ts).
type RouteContext = { params: Promise<{ token: string }> };

/** Relay an upstream problem+json response verbatim (status + body, untouched). */
async function relayUpstreamError(res: Response): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = { title: "Upstream error", status: res.status };
  }
  return NextResponse.json(body, { status: res.status });
}

export async function GET(_req: NextRequest, context: RouteContext): Promise<NextResponse> {
  const { token } = await context.params;

  const previewRes = await fetch(`${gatewayUrl()}/invites/${encodeURIComponent(token)}`, {
    method: "GET",
  });

  if (!previewRes.ok) return relayUpstreamError(previewRes);

  const body = await previewRes.json();
  return NextResponse.json(body, { status: 200 });
}

export async function POST(req: NextRequest, context: RouteContext): Promise<NextResponse> {
  const { token } = await context.params;

  const parsed = await parseJsonBody(req, acceptInviteSchema);
  if (!parsed.ok) return parsed.response;
  const { password } = parsed.data;

  // Step 1: accept the invite (creates the user, tenant membership).
  const acceptRes = await fetch(`${gatewayUrl()}/invites/${encodeURIComponent(token)}/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });

  if (!acceptRes.ok) return relayUpstreamError(acceptRes);

  const accepted = (await acceptRes.json()) as { tenant_id: string; user_id: string; email: string };

  // Step 2: auto-login to get the session JWT — same chain as signup.
  const loginRes = await fetch(`${gatewayUrl()}/admin/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: accepted.email, password }),
  });

  if (!loginRes.ok) return relayUpstreamError(loginRes);

  const { access_token: jwt } = (await loginRes.json()) as { access_token: string };

  const res = NextResponse.json({ ok: true }, { status: 201 });
  res.headers.set("Set-Cookie", buildSessionCookieValue(jwt));
  return res;
}
