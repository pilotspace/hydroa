/**
 * POST /api/auth/join/[token]
 *
 * PUBLIC (unauthenticated) BFF route — phase 1 of the domain-invite-link
 * redeem flow (invite-by-domain-ui TASK.md §3 CONTRACT — FROZEN @ v1, M8).
 * Mirrors app/api/auth/invite/[token]/route.ts's gatewayUrl()/relay pattern,
 * but takes the invite-link token as a PATH param and forwards ONLY the
 * email — the UI never sends or derives domain/tenant/role (M8).
 *
 * POST {email} -> gateway POST /domain-invite-links/{token}/redeem -> 202
 * {email} passthrough. A 4xx (404 ERR_INVITE_NOT_FOUND / 409
 * ERR_DOMAIN_INVITE_LINK_INACTIVE / 410 ERR_INVITE_EXPIRED / 403
 * ERR_DOMAIN_INVITE_DOMAIN_MISMATCH / 429 ERR_RATE_LIMITED) relays verbatim.
 */

import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { parseJsonBody } from "@/lib/bff-validation";

// Inline per the frozen §5 scope note (the shared bff-validation module is not
// declared in this task's scope) — guard-bound presence + length only; the
// gateway is the redeem-eligibility authority.
const domainRedeemSchema = z.object({
  email: z.string().min(1).max(320),
});

function gatewayUrl(): string {
  // Server-side only: a NEXT_PUBLIC_-prefixed var would be inlined into the
  // client bundle, leaking the in-cluster gateway address to browsers.
  return process.env.GATEWAY_URL ?? "http://localhost:8080";
}

// Next.js 15/16 App Router: params is always a Promise. Unit tests pass a plain
// object which is also thenable via Promise.resolve (mirrors the invite route).
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

export async function POST(req: NextRequest, context: RouteContext): Promise<NextResponse> {
  const { token } = await context.params;

  const parsed = await parseJsonBody(req, domainRedeemSchema);
  if (!parsed.ok) return parsed.response;
  const { email } = parsed.data;

  const redeemRes = await fetch(
    `${gatewayUrl()}/domain-invite-links/${encodeURIComponent(token)}/redeem`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    },
  );

  if (!redeemRes.ok) return relayUpstreamError(redeemRes);

  const body = await redeemRes.json();
  return NextResponse.json(body, { status: 202 });
}
