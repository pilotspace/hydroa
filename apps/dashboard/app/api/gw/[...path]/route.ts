/**
 * GET|POST|PUT|PATCH|DELETE /api/gw/[...path]
 *
 * BFF authenticated gateway proxy — reads ai_proxy_session cookie, attaches
 * Authorization: Bearer header server-side, forwards to GATEWAY_URL.
 *
 * On upstream 401: clears the cookie and returns 401 ERR_AUTH_SESSION_EXPIRED.
 * On absent cookie: returns 401 ERR_AUTH_NO_SESSION without calling upstream.
 *
 * STREAMING (v40 streaming-bff, §3 frozen contract): a STREAMABLE upstream
 * response (Content-Type in {text/event-stream, audio/*, video/*,
 * application/octet-stream}, OR the request asked stream:true and the upstream is
 * NOT application/json) is piped to the client UNBUFFERED — `upstream.json()` is
 * never called on it. The upstream fetch is driven by an AbortController so a
 * client disconnect (req.signal) OR a response-stream cancel tears the upstream
 * down and the gateway's v35 disconnect-billing fires. An application/json body is
 * NEVER piped, so pre-stream JSON errors (402/429) to a stream:true request stay
 * buffered. All other responses: proxied verbatim (byte-identical to before v40).
 */

import { NextRequest, NextResponse } from "next/server";

// A response is streamed when its upstream Content-Type matches this allowlist
// (startsWith), or the request asked stream:true and the upstream is not JSON.
const STREAMABLE_CONTENT_TYPES = [
  "text/event-stream",
  "audio/",
  "video/",
  "application/octet-stream",
];

// Hop-by-hop / body-framing headers dropped before forwarding a streamed
// response: fetch already decoded the body, so content-encoding/content-length
// would corrupt the piped bytes; connection-level headers are per-connection.
const HOP_BY_HOP_HEADERS = [
  "content-encoding",
  "content-length",
  "transfer-encoding",
  "connection",
  "keep-alive",
];

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
  let bodyText: string | null = null;
  const method = req.method;
  const isJsonBody = !contentType || contentType.startsWith("application/json");
  let bodyInit: BodyInit | undefined = undefined; // BodyInit covers string | ArrayBuffer
  if (method !== "GET" && method !== "HEAD" && method !== "DELETE") {
    if (isJsonBody) {
      try {
        bodyText = await req.text();
        bodyInit = bodyText ?? undefined;
      } catch {
        bodyText = null;
        bodyInit = undefined;
      }
    } else {
      // binary / multipart (e.g. STT file upload): forward raw bytes — req.text() would UTF-8-decode & corrupt it.
      try {
        bodyInit = await req.arrayBuffer();
      } catch {
        bodyInit = undefined;
      }
    }
  }

  // Belt-and-suspenders stream signal: did the caller ask for stream:true?
  // Best-effort — a non-JSON body simply isn't a stream request.
  let requestedStream = false;
  if (bodyText) {
    try {
      const parsed = JSON.parse(bodyText) as { stream?: unknown };
      requestedStream = parsed?.stream === true;
    } catch {
      requestedStream = false;
    }
  }

  // Drive the upstream fetch with an explicit AbortController so a client
  // disconnect (req.signal) OR a response-stream cancel aborts the upstream and
  // the gateway's v35 disconnect-billing fires (M2).
  const controller = new AbortController();
  if (req.signal.aborted) {
    controller.abort();
  } else {
    req.signal.addEventListener("abort", () => controller.abort());
  }

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, {
      method,
      headers: upstreamHeaders,
      body: bodyInit,
      signal: controller.signal,
    });
  } catch (err) {
    // Client disconnected before/while awaiting the upstream: the abort already
    // propagated to the gateway (v35 disconnect-billing fires) and there is no
    // client left to serve — return a graceful 499 instead of throwing a 500.
    if (controller.signal.aborted) {
      return new NextResponse(null, { status: 499 });
    }
    throw err; // genuine network error — preserve prior behavior
  }

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

  // Decide stream-vs-buffer. An application/json body is NEVER piped, so a
  // pre-stream JSON error (402/429) to a stream:true request stays buffered (M6).
  const upstreamContentType = (upstream.headers.get("content-type") ?? "").toLowerCase();
  const isJson = upstreamContentType.startsWith("application/json");
  const shouldStream =
    STREAMABLE_CONTENT_TYPES.some((prefix) => upstreamContentType.startsWith(prefix)) ||
    (requestedStream && !isJson);

  if (shouldStream && upstream.body) {
    // Forward upstream headers minus hop-by-hop; add no-buffering hints (M3).
    const headers = new Headers(upstream.headers);
    for (const name of HOP_BY_HOP_HEADERS) {
      headers.delete(name);
    }
    // The BFF owns the session cookie — never relay an upstream-set browser
    // cookie (parity with the buffered path, which drops all upstream headers).
    headers.delete("set-cookie");
    headers.set("Cache-Control", "no-cache");
    headers.set("X-Accel-Buffering", "no");

    // Wrap the upstream body so a client cancel propagates to the controller (M2).
    // `cancelled` guards every controller op so a cancel/pull race never calls
    // close()/error() on an already-cancelled stream (no unhandled rejection).
    const reader = upstream.body.getReader();
    let cancelled = false;
    const piped = new ReadableStream<Uint8Array>({
      async pull(streamController) {
        if (cancelled) return;
        try {
          const { done, value } = await reader.read();
          if (cancelled) return;
          if (done) {
            streamController.close();
            return;
          }
          streamController.enqueue(value);
        } catch (err) {
          if (!cancelled) streamController.error(err);
        }
      },
      cancel(reason) {
        cancelled = true;
        controller.abort();
        void reader.cancel(reason).catch(() => {});
      },
    });

    return new NextResponse(piped, { status: upstream.status, headers });
  }

  // Proxy all non-streamable responses verbatim (buffered JSON — byte-identical
  // to before v40).
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
