/**
 * GET /api/auth/me
 *
 * BFF route handler — reads ai_proxy_session cookie, decodes JWT payload
 * server-side (no signature verification), returns claims.
 * Never returns the raw JWT string.
 */

import { NextRequest, NextResponse } from "next/server";

interface JwtPayload {
  sub?: string;
  user_id?: string;
  tenant_id?: string;
  email?: string;
  role?: string;
  exp?: number;
}

function decodeJwtPayload(token: string): JwtPayload | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const raw = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    // Add padding if needed
    const padded = raw + "=".repeat((4 - (raw.length % 4)) % 4);
    return JSON.parse(Buffer.from(padded, "base64").toString("utf8")) as JwtPayload;
  } catch {
    return null;
  }
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const cookieHeader = req.headers.get("cookie") ?? "";
  const match = cookieHeader.match(/ai_proxy_session=([^;]+)/);
  const token = match?.[1];

  if (!token) {
    return NextResponse.json(
      { code: "ERR_AUTH_NO_SESSION" },
      { status: 401 }
    );
  }

  const payload = decodeJwtPayload(token);
  if (!payload) {
    return NextResponse.json(
      { code: "ERR_AUTH_NO_SESSION" },
      { status: 401 }
    );
  }

  return NextResponse.json({
    user_id: payload.user_id ?? payload.sub ?? null,
    tenant_id: payload.tenant_id ?? null,
    email: payload.email ?? null,
    role: payload.role ?? null,
    exp: payload.exp ?? null,
  });
}
