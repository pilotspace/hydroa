/**
 * GET|POST|PUT|PATCH|DELETE /api/gw/[...path]
 *
 * BFF authenticated gateway proxy — reads ai_proxy_session cookie, attaches
 * Authorization: Bearer header server-side, forwards to GATEWAY_URL.
 *
 * On a CONTROL-PLANE (/admin/*) upstream 401: clears the cookie and returns 401
 * ERR_AUTH_SESSION_EXPIRED (the session JWT is invalid/expired). On a DATA-PLANE
 * (/v1/*) upstream 401: passes the upstream error through verbatim WITHOUT clearing
 * the cookie (the session JWT was rejected as an API key, not a session expiry —
 * never log the user out for that). On absent cookie: returns 401 ERR_AUTH_NO_SESSION
 * without calling upstream.
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
import { getPlaygroundToken, invalidatePlaygroundToken } from "@/lib/playground-token";

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
  // Server-side only: read the non-public GATEWAY_URL. A NEXT_PUBLIC_-prefixed var would be
  // inlined into the client bundle, leaking the in-cluster gateway address to browsers.
  return process.env.GATEWAY_URL ?? "http://localhost:8080";
}

/** Server-side upstream timeout (ms); env-overridable, read at call time. */
function serverTimeoutMs(): number {
  const raw = process.env.GATEWAY_PROXY_TIMEOUT_MS;
  const n = raw ? Number(raw) : NaN;
  return Number.isFinite(n) && n > 0 ? n : 15_000;
}

/**
 * Max request body bytes forwarded upstream; env-overridable (GW_MAX_BODY_BYTES).
 * Default 32 MiB — a coarse DoS guard that blocks pathological large bodies while
 * admitting the platform's legitimate uploads that flow through this proxy:
 * base64 artifacts (gateway cap 10 MiB → ~13.3 MiB encoded), Gemini inline media
 * (20 MiB), and audio/realtime utterances (25 MiB). The gateway enforces the
 * precise per-feature ceilings; this is the outer envelope.
 */
function maxBodyBytes(): number {
  const raw = process.env.GW_MAX_BODY_BYTES;
  const n = raw ? Number(raw) : NaN;
  return Number.isFinite(n) && n > 0 ? n : 33_554_432;
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

  // CONTROL-PLANE (/admin/*) uses the session JWT directly. DATA-PLANE (/v1/*)
  // needs an sk-/agent key the cookie can't carry, so the BFF does a SERVER-SIDE
  // token exchange: mint (and cache) a short-lived, spend-capped playground key
  // and forward THAT. The key never reaches the browser. On a mint miss we fall
  // through with the JWT — the data plane rejects it inline (no logout), the same
  // honest degrade as before this wiring.
  const isDataPlane = pathStr === "v1" || pathStr.startsWith("v1/");
  let upstreamToken = token;
  if (isDataPlane) {
    const playgroundKey = await getPlaygroundToken(token, gatewayUrl());
    if (playgroundKey) upstreamToken = playgroundKey;
  }

  // Forward query string
  const { searchParams } = new URL(req.url);
  const queryString = searchParams.toString();
  const upstreamUrl = `${gatewayUrl()}/${pathStr}${queryString ? `?${queryString}` : ""}`;

  // Build upstream headers — attach Bearer, forward Content-Type if present
  const upstreamHeaders: Record<string, string> = {
    Authorization: `Bearer ${upstreamToken}`,
  };
  const contentType = req.headers.get("content-type");
  if (contentType) {
    upstreamHeaders["Content-Type"] = contentType;
  }

  // Forward body for mutating methods, fail-closed on oversized payloads. The
  // Content-Length header is an early reject; the read byte length is the
  // authoritative check (a lying/absent header can't smuggle a large body).
  // JSON bodies are read as text (also reused for stream:true detection); binary
  // / multipart (e.g. STT file upload) is forwarded as raw bytes — req.text()
  // would UTF-8-decode & corrupt it.
  const cap = maxBodyBytes();
  let bodyText: string | null = null;
  const method = req.method;
  const isJsonBody = !contentType || contentType.startsWith("application/json");
  let bodyInit: BodyInit | undefined = undefined; // BodyInit covers string | ArrayBuffer
  if (method !== "GET" && method !== "HEAD" && method !== "DELETE") {
    const declared = Number(req.headers.get("content-length") ?? "");
    if (Number.isFinite(declared) && declared > cap) {
      return NextResponse.json(
        { code: "ERR_BFF_PAYLOAD_TOO_LARGE" },
        { status: 413 }
      );
    }
    if (isJsonBody) {
      try {
        bodyText = await req.text();
        bodyInit = bodyText ?? undefined;
      } catch {
        bodyText = null;
        bodyInit = undefined;
      }
      if (bodyText && new TextEncoder().encode(bodyText).length > cap) {
        return NextResponse.json(
          { code: "ERR_BFF_PAYLOAD_TOO_LARGE" },
          { status: 413 }
        );
      }
    } else {
      // binary / multipart (e.g. STT file upload): forward raw bytes.
      try {
        const buf = await req.arrayBuffer();
        if (buf.byteLength > cap) {
          return NextResponse.json(
            { code: "ERR_BFF_PAYLOAD_TOO_LARGE" },
            { status: 413 }
          );
        }
        bodyInit = buf;
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
  // the gateway's v35 disconnect-billing fires (M2). A server-side timeout bounds
  // how long we wait for the upstream RESPONSE HEADERS, then is cleared once they
  // arrive so a long-lived SSE/stream body is never killed mid-flight.
  const controller = new AbortController();
  if (req.signal.aborted) {
    controller.abort();
  } else {
    req.signal.addEventListener("abort", () => controller.abort());
  }
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, serverTimeoutMs());

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, {
      method,
      headers: upstreamHeaders,
      body: bodyInit,
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timeout);
    // The abort already propagated to the gateway (v35 disconnect-billing fires).
    // Distinguish OUR header-phase timeout (504) from a client disconnect (499);
    // any other transport error is a typed 502, never an unhandled 500.
    if (timedOut) {
      return NextResponse.json({ code: "ERR_BFF_TIMEOUT" }, { status: 504 });
    }
    if (controller.signal.aborted) {
      return new NextResponse(null, { status: 499 });
    }
    return NextResponse.json({ code: "ERR_BFF_NETWORK" }, { status: 502 });
  }
  // Response headers arrived — stop the header-phase timeout so a streaming body
  // can outlive it.
  clearTimeout(timeout);

  // On upstream 401: only a CONTROL-PLANE (/admin/*) 401 means the session JWT
  // itself is invalid/expired — clear the cookie and signal ERR_AUTH_SESSION_EXPIRED
  // so the client bounces to /login. A DATA-PLANE (/v1/*) 401 means the session JWT
  // was rejected as an API key (the data plane requires an sk-/agent token); the
  // SESSION is fine, so clearing the cookie here would log the user out just for
  // opening a playground page. Pass that 401 through verbatim (buffered branch below)
  // with the upstream's own error code, cookie intact.
  const isControlPlanePath = pathStr === "admin" || pathStr.startsWith("admin/");
  if (upstream.status === 401 && isControlPlanePath) {
    const res = NextResponse.json(
      { code: "ERR_AUTH_SESSION_EXPIRED" },
      { status: 401 }
    );
    res.headers.set("Set-Cookie", buildClearCookieValue());
    return res;
  }
  // A DATA-PLANE 401 means the minted playground key was rejected (expired/revoked
  // mid-flight). Drop it so the next request re-mints; the cookie stays intact.
  if (upstream.status === 401 && isDataPlane) {
    invalidatePlaygroundToken(token);
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

  // Binary passthrough: non-JSON, non-streaming responses with a body are
  // forwarded verbatim (e.g. artifact downloads). The json() fallback below
  // would coerce them to null — this branch prevents that.
  const isBinaryPassthrough = !isJson && !shouldStream && upstream.body != null;
  if (isBinaryPassthrough) {
    const buf = await upstream.arrayBuffer();
    const headers = new Headers(upstream.headers);
    for (const name of HOP_BY_HOP_HEADERS) headers.delete(name);
    headers.delete("set-cookie");
    return new NextResponse(buf, { status: upstream.status, headers });
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
