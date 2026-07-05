/**
 * POST /api/platform/impersonation (Mint) + GET /api/platform/impersonation (Status)
 *
 * impersonation-ui TASK.md §3 CONTRACT Part A. Mirrors this codebase's existing BFF
 * route conventions (`app/api/auth/login/route.ts`'s cookie-set shape,
 * `app/api/auth/me/route.ts`'s relay-verify shape) rather than inventing new ones.
 *
 * Mint: relays ONLY `ai_proxy_session` (the original superadmin's own session) as
 * `Authorization: Bearer` to the gateway's `mint_impersonation_session`. On 201,
 * builds the M1 cookie envelope from the gateway's OWN {token, session_id,
 * expires_in} and sets it as `ai_proxy_impersonation_session` — the token itself
 * never appears in the JSON response body (mirrors `/api/auth/login`'s own "never
 * exposes the JWT in the response body" rule). On any upstream 4xx: relays status +
 * body verbatim, zero BFF-invented codes (R2), no cookie set.
 *
 * Status: reads the impersonation envelope (if present) and relays its `.token` to
 * `GET /admin/auth/me` for the VERIFIED target identity; separately relays
 * `ai_proxy_session` (if present) to the SAME endpoint for the VERIFIED actor
 * identity. `session_id`/`expires_at` come from the envelope directly — `GET /me`
 * carries neither — so both survive a fresh page load with zero gateway change
 * (M1/M4). Never non-200: an unreadable/rejected impersonation cookie self-heals to
 * `{active:false}` + a Set-Cookie clearing it, never an error response.
 */

import { NextRequest, NextResponse } from "next/server";
import {
  buildClearImpersonationCookie,
  buildImpersonationCookie,
  decodeImpersonationEnvelope,
  readRawImpersonationCookie,
} from "@/lib/impersonation-cookie";

function gatewayUrl(): string {
  // Server-side only: read the non-public GATEWAY_URL. A NEXT_PUBLIC_-prefixed var would be
  // inlined into the client bundle, leaking the in-cluster gateway address to browsers.
  return process.env.GATEWAY_URL ?? "http://localhost:8080";
}

/** Upstream timeout (ms) — mirrors /api/auth/me's own design-for-failure bound. */
const UPSTREAM_TIMEOUT_MS = 5000;

/** Reads ONLY ai_proxy_session — the ORIGINAL superadmin's own session (never the
 * impersonation cookie), mirroring the existing /api/auth/me convention exactly. */
function readOriginalToken(req: NextRequest): string | null {
  const cookieHeader = req.headers.get("cookie") ?? "";
  const match = cookieHeader.match(/ai_proxy_session=([^;]+)/);
  return match?.[1]?.trim() || null;
}

interface ImpersonationTarget {
  user_id: string;
  tenant_id: string;
  email: string;
  role: string;
}

interface MintUpstreamResponse {
  token: string;
  expires_in: number;
  session_id: string;
  target: ImpersonationTarget;
}

// ── POST (Mint) ──────────────────────────────────────────────────────────────

export async function POST(req: NextRequest): Promise<NextResponse> {
  const originalToken = readOriginalToken(req);
  if (!originalToken) {
    // R1 — never calls upstream when the original session cookie is absent.
    return NextResponse.json({ code: "ERR_AUTH_NO_SESSION" }, { status: 401 });
  }

  let body: { tenant_id?: unknown; user_id?: unknown };
  try {
    body = (await req.json()) as { tenant_id?: unknown; user_id?: unknown };
  } catch {
    body = {};
  }
  const tenantId = typeof body.tenant_id === "string" ? body.tenant_id : "";
  const userId = typeof body.user_id === "string" ? body.user_id : "";

  let upstream: Response;
  try {
    upstream = await fetch(
      `${gatewayUrl()}/admin/platform/tenants/${encodeURIComponent(tenantId)}/users/${encodeURIComponent(userId)}/impersonate`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${originalToken}` },
        signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      },
    );
  } catch {
    return NextResponse.json({ code: "ERR_BFF_UPSTREAM" }, { status: 503 });
  }

  if (!upstream.ok) {
    // R2 — relay the gateway's own 4xx verbatim, zero BFF-invented codes, no cookie.
    let errorBody: unknown;
    try {
      errorBody = await upstream.json();
    } catch {
      errorBody = { title: "Upstream error", status: upstream.status };
    }
    return NextResponse.json(errorBody, { status: upstream.status });
  }

  const data = (await upstream.json()) as MintUpstreamResponse;

  // The token itself is inside the Set-Cookie envelope ONLY — never the JSON body
  // (M1/M2). Only session_id/expires_in/target are echoed back to the client.
  const res = NextResponse.json(
    {
      session_id: data.session_id,
      expires_in: data.expires_in,
      target: data.target,
    },
    { status: 201 },
  );
  res.headers.set(
    "Set-Cookie",
    buildImpersonationCookie(
      {
        token: data.token,
        session_id: data.session_id,
        expires_at: Date.now() + data.expires_in * 1000,
      },
      data.expires_in,
    ),
  );
  return res;
}

// ── GET (Status) ─────────────────────────────────────────────────────────────

interface MeUpstream {
  user_id: string;
  tenant_id: string;
  email: string;
  role: string;
}

/** Relay a bearer token to GET /admin/auth/me. Any non-200 (including a network
 * failure) degrades to `null` — the caller decides what "no identity" means for
 * its own branch (self-heal for the target, actor:null for the actor). */
async function relayMe(token: string): Promise<MeUpstream | null> {
  try {
    const upstream = await fetch(`${gatewayUrl()}/admin/auth/me`, {
      method: "GET",
      headers: { Authorization: `Bearer ${token}` },
      // Do NOT follow redirects — mirrors /api/auth/me's own v18 hardening: a 3xx
      // must never chain to a 200 from an unexpected origin treated as trusted.
      redirect: "manual",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    if (!upstream.ok) return null;
    return (await upstream.json()) as MeUpstream;
  } catch {
    return null;
  }
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const cookieHeader = req.headers.get("cookie") ?? "";
  const rawEnvelope = readRawImpersonationCookie(cookieHeader);

  if (!rawEnvelope) {
    // Nothing to resolve — neither relay call is worth making, nothing to clear.
    return NextResponse.json({ active: false });
  }

  const envelope = decodeImpersonationEnvelope(rawEnvelope);
  if (!envelope) {
    // Present but malformed (M1 edge case): self-heal — clear it, never a 500.
    const res = NextResponse.json({ active: false });
    res.headers.set("Set-Cookie", buildClearImpersonationCookie());
    return res;
  }

  const target = await relayMe(envelope.token);
  if (!target) {
    // The impersonation token itself is invalid/rejected upstream: self-heal
    // (mirrors the gw-proxy's own "control-plane 401 clears the cookie" convention).
    const res = NextResponse.json({ active: false });
    res.headers.set("Set-Cookie", buildClearImpersonationCookie());
    return res;
  }

  const originalToken = readOriginalToken(req);
  const actorMe = originalToken ? await relayMe(originalToken) : null;

  return NextResponse.json({
    active: true,
    session_id: envelope.session_id,
    expires_at: envelope.expires_at,
    target: {
      user_id: target.user_id,
      tenant_id: target.tenant_id,
      email: target.email,
      role: target.role,
    },
    // Partial info beats a hard failure — target/session_id/expires_at/End control
    // all remain fully correct even when the actor relay fails or is absent.
    actor: actorMe ? { email: actorMe.email, role: actorMe.role } : null,
  });
}
