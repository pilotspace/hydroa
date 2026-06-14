/**
 * GET|POST|PUT|DELETE /api/gw/[...path]
 *
 * BFF authenticated gateway proxy — reads ai_proxy_session cookie, attaches
 * Authorization: Bearer header server-side, forwards to GATEWAY_URL.
 *
 * On upstream 401: clears the cookie and returns 401 ERR_AUTH_SESSION_EXPIRED.
 * On absent cookie: returns 401 ERR_AUTH_NO_SESSION without calling upstream.
 * All other responses: proxied verbatim.
 */

import { NextRequest, NextResponse } from "next/server";

function gatewayUrl(): string {
  return (
    process.env.GATEWAY_URL ??
    process.env.NEXT_PUBLIC_GATEWAY_URL ??
    "http://localhost:8080"
  );
}

function buildClearCookieValue(): string {
  const secure = process.env.NODE_ENV !== "development" ? "; Secure" : "";
  return `ai_proxy_session=; HttpOnly${secure}; SameSite=Strict; Path=/; Max-Age=0`;
}

function getTokenFromRequest(req: NextRequest): string | null {
  const cookieHeader = req.headers.get("cookie") ?? "";
  const match = cookieHeader.match(/ai_proxy_session=([^;]+)/);
  return match?.[1] ?? null;
}

// Next.js 15 App Router: params is always a Promise.
// Unit tests pass a plain object which is also thenable via Promise.resolve.
type RouteContext = { params: Promise<{ path: string[] }> };

async function proxyRequest(
  req: NextRequest,
  context: RouteContext
): Promise<NextResponse> {
  const token = getTokenFromRequest(req);

  if (!token) {
    return NextResponse.json(
      { code: "ERR_AUTH_NO_SESSION" },
      { status: 401 }
    );
  }

  // Await params — Next.js 15 always passes params as a Promise
  const resolvedParams = await context.params;
  const pathSegments = resolvedParams.path;
  const pathStr = pathSegments.join("/");

  // Forward query string
  const { searchParams } = new URL(req.url);
  const queryString = searchParams.toString();
  const upstreamUrl = `${gatewayUrl()}/${pathStr}${queryString ? `?${queryString}` : ""}`;

  // Build upstream headers — attach Bearer, forward Content-Type if present
  const upstreamHeaders: Record<string, string> = {
    Authorization: `Bearer ${token}`,
  };
  const contentType = req.headers.get("content-type");
  if (contentType) {
    upstreamHeaders["Content-Type"] = contentType;
  }

  // Forward body for mutating methods
  let upstreamBody: BodyInit | null = null;
  const method = req.method;
  if (method !== "GET" && method !== "HEAD" && method !== "DELETE") {
    try {
      upstreamBody = await req.text();
    } catch {
      upstreamBody = null;
    }
  }

  const upstream = await fetch(upstreamUrl, {
    method,
    headers: upstreamHeaders,
    body: upstreamBody ?? undefined,
  });

  // On upstream 401: clear cookie, return ERR_AUTH_SESSION_EXPIRED
  if (upstream.status === 401) {
    const res = NextResponse.json(
      { code: "ERR_AUTH_SESSION_EXPIRED" },
      { status: 401 }
    );
    res.headers.set("Set-Cookie", buildClearCookieValue());
    return res;
  }

  // 204 No Content
  if (upstream.status === 204) {
    return new NextResponse(null, { status: 204 });
  }

  // Proxy all other responses verbatim
  let responseBody: unknown;
  try {
    responseBody = await upstream.json();
  } catch {
    responseBody = null;
  }

  return NextResponse.json(responseBody, { status: upstream.status });
}

export async function GET(req: NextRequest, context: RouteContext): Promise<NextResponse> {
  return proxyRequest(req, context);
}

export async function POST(req: NextRequest, context: RouteContext): Promise<NextResponse> {
  return proxyRequest(req, context);
}

export async function PUT(req: NextRequest, context: RouteContext): Promise<NextResponse> {
  return proxyRequest(req, context);
}

export async function PATCH(req: NextRequest, context: RouteContext): Promise<NextResponse> {
  return proxyRequest(req, context);
}

export async function DELETE(req: NextRequest, context: RouteContext): Promise<NextResponse> {
  return proxyRequest(req, context);
}
