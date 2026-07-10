/**
 * DELETE /api/platform/impersonation/sessions/{sessionId} (End)
 *
 * impersonation-ui TASK.md §3 CONTRACT Part A / Safety rule (§5): the upstream
 * Authorization header is built from `ai_proxy_session` ONLY — unconditionally,
 * even when `ai_proxy_impersonation_session` is ALSO present on the same request.
 * This is the client-side mirror of the sibling impersonation-session-lifecycle
 * task's own frozen, security-reviewed "End requires the ORIGINAL JWT" contract —
 * getting this branch wrong would defeat the entire reason two cookies exist (an
 * impersonated identity ending its OWN session, using its own token, would not be
 * the superadmin's explicit act). Non-negotiable; there is no fallback path here to
 * the impersonation cookie, by construction — this file never reads or references
 * `ai_proxy_impersonation_session` at all, only ever clears it on success.
 */

import { NextRequest, NextResponse } from "next/server";
import { buildClearImpersonationCookie } from "@/lib/impersonation-cookie";

function gatewayUrl(): string {
  return process.env.GATEWAY_URL ?? "http://localhost:8080";
}

const UPSTREAM_TIMEOUT_MS = 5000;

function readOriginalToken(req: NextRequest): string | null {
  const cookieHeader = req.headers.get("cookie") ?? "";
  const match = cookieHeader.match(/ai_proxy_session=([^;]+)/);
  return match?.[1]?.trim() || null;
}

// Next.js 15 App Router: params is always a Promise (mirrors app/api/gw/[...path]).
type RouteContext = { params: Promise<{ sessionId: string }> };

export async function DELETE(req: NextRequest, context: RouteContext): Promise<NextResponse> {
  const token = readOriginalToken(req);
  if (!token) {
    // R1 — never calls upstream when the original session cookie is absent, even
    // if an impersonation cookie is present on the same request.
    return NextResponse.json({ code: "ERR_AUTH_NO_SESSION" }, { status: 401 });
  }

  const { sessionId } = await context.params;

  let upstream: Response;
  try {
    upstream = await fetch(
      `${gatewayUrl()}/admin/platform/impersonation/sessions/${encodeURIComponent(sessionId)}`,
      {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
        signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      },
    );
  } catch {
    return NextResponse.json({ code: "ERR_BFF_UPSTREAM" }, { status: 503 });
  }

  if (upstream.status === 204) {
    const res = new NextResponse(null, { status: 204 });
    res.headers.set("Set-Cookie", buildClearImpersonationCookie());
    return res;
  }

  // Any other status (the gateway's own 4xx, R2): relay verbatim, impersonation
  // cookie left UNTOUCHED — an already-ended/expired session on the server should
  // not have its client cookie silently cleared behind a 409, so a retry against
  // the same stale cookie stays diagnosable.
  let errorBody: unknown;
  try {
    errorBody = await upstream.json();
  } catch {
    errorBody = { title: "Upstream error", status: upstream.status };
  }
  return NextResponse.json(errorBody, { status: upstream.status });
}
