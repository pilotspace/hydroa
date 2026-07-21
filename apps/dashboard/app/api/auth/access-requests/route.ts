/**
 * POST /api/auth/access-requests
 *
 * PUBLIC (unauthenticated) BFF route — signup-refusal-router TASK.md §3
 * CONTRACT (FROZEN @ v1, M4). Mirrors app/api/auth/join/[token]/route.ts's
 * gatewayUrl()/relay pattern: proxies {email} verbatim to the gateway's new
 * POST /admin/auth/access-requests and relays its status + body UNCHANGED —
 * a 202 uniform success, a 422 malformed-email validation error, or a 429
 * ERR_RATE_LIMITED all pass through byte-for-byte (R3: the BFF layer must not
 * itself introduce any asymmetry between a "known" and "unknown" email).
 *
 * Unlike app/api/auth/signup/route.ts, this route NEVER sets the session
 * cookie and NEVER calls /admin/auth/login — a visitor requesting access has
 * no session to create; this is a lead-capture, not an auth event.
 */

import { NextRequest, NextResponse } from "next/server";
import { accessRequestSchema, parseJsonBody } from "@/lib/bff-validation";

function gatewayUrl(): string {
  // Server-side only: a NEXT_PUBLIC_-prefixed var would be inlined into the
  // client bundle, leaking the in-cluster gateway address to browsers.
  return process.env.GATEWAY_URL ?? "http://localhost:8080";
}

/** Relay an upstream problem+json (or pydantic validation) response verbatim. */
async function relayUpstreamError(res: Response): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = { title: "Upstream error", status: res.status };
  }
  return NextResponse.json(body, { status: res.status });
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const parsed = await parseJsonBody(req, accessRequestSchema);
  if (!parsed.ok) return parsed.response;
  const { email } = parsed.data;

  const upstreamRes = await fetch(`${gatewayUrl()}/admin/auth/access-requests`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });

  if (!upstreamRes.ok) return relayUpstreamError(upstreamRes);

  const body = await upstreamRes.json();
  return NextResponse.json(body, { status: upstreamRes.status });
}
