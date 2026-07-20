/**
 * POST /api/auth/join/[token]/verify
 *
 * PUBLIC (unauthenticated) BFF route — phase 2 of the domain-invite-link
 * redeem flow (invite-by-domain-ui TASK.md §3 CONTRACT — FROZEN @ v1, M9/M10).
 * Mirrors app/api/auth/invite/[token]/route.ts's two-step accept-then-login
 * chain, but the SECOND step (chained login) is fail-soft here: the gateway
 * verify call already created the account/membership, so a login failure is
 * never relayed as an upstream error — it degrades to `{ok:true,
 * session:false}` (R8) and the page hands off to /login. The JWT never
 * appears in the response body.
 *
 * POST {email, code, password} -> gateway POST
 * /domain-invite-links/{token}/redeem/verify.
 *   - Upstream 4xx (400 ERR_MEMBER_VERIFY_CODE_INVALID / ERR_AUTH_PASSWORD_WEAK
 *     / 410 ERR_MEMBER_VERIFY_CODE_EXPIRED / 429 ERR_MEMBER_VERIFY_TOO_MANY_
 *     ATTEMPTS / 409 ERR_TENANT_EMAIL_TAKEN / 403 ERR_PLAN_SEAT_CAP_EXCEEDED /
 *     403 ERR_DOMAIN_INVITE_DOMAIN_MISMATCH / 409
 *     ERR_DOMAIN_INVITE_LINK_INACTIVE / 410 ERR_INVITE_EXPIRED / 404
 *     ERR_INVITE_NOT_FOUND / 429 ERR_RATE_LIMITED) relays verbatim — NO login
 *     is attempted.
 *   - Upstream 201 {tenant_id,user_id,email} -> chain gateway POST
 *     /admin/auth/login {email,password}. Login OK -> Set-Cookie
 *     ai_proxy_session + {ok:true,session:true}. Login fails -> {ok:true,
 *     session:false}, no cookie (R8 — never stranded, the page routes to
 *     /login with a "you've joined" hint).
 */

import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { parseJsonBody } from "@/lib/bff-validation";

// Inline per the frozen §5 scope note (the shared bff-validation module is not
// declared in this task's scope) — guard-bound presence + length only; the
// gateway is the password-policy and code authority.
const domainRedeemVerifySchema = z.object({
  email: z.string().min(1).max(320),
  code: z.string().min(1),
  password: z.string().min(1).max(4096),
});

function gatewayUrl(): string {
  return process.env.GATEWAY_URL ?? "http://localhost:8080";
}

function buildSessionCookieValue(jwt: string): string {
  const secure = process.env.NODE_ENV !== "development" ? "; Secure" : "";
  return `ai_proxy_session=${jwt}; HttpOnly${secure}; SameSite=Strict; Path=/; Max-Age=86400`;
}

type RouteContext = { params: Promise<{ token: string }> };

async function relayUpstreamError(res: Response): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = { title: "Upstream error", status: res.status };
  }
  return NextResponse.json(body, { status: res.status });
}

export async function POST(req: NextRequest, context: RouteContext): Promise<NextResponse> {
  const { token } = await context.params;

  const parsed = await parseJsonBody(req, domainRedeemVerifySchema);
  if (!parsed.ok) return parsed.response;
  const { email, code, password } = parsed.data;

  const verifyRes = await fetch(
    `${gatewayUrl()}/domain-invite-links/${encodeURIComponent(token)}/redeem/verify`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, code, password }),
    },
  );

  if (!verifyRes.ok) return relayUpstreamError(verifyRes);

  // The account/membership now exists — chain a login with the SAME
  // credentials to establish the session. A failure here is never a
  // redeem/verify error; the user has already joined (R8).
  const loginRes = await fetch(`${gatewayUrl()}/admin/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  if (!loginRes.ok) {
    return NextResponse.json({ ok: true, session: false }, { status: 201 });
  }

  const { access_token: jwt } = (await loginRes.json()) as { access_token: string };

  const res = NextResponse.json({ ok: true, session: true }, { status: 201 });
  res.headers.set("Set-Cookie", buildSessionCookieValue(jwt));
  return res;
}
