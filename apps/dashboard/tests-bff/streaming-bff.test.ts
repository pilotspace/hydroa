/**
 * tests-bff/streaming-bff.test.ts — RED suite for v40 streaming-bff.
 *
 * The BFF catch-all proxy (app/api/gw/[...path]/route.ts) currently buffers EVERY
 * upstream response via `await upstream.json()`, so SSE/binary never streams and
 * client disconnect never reaches the upstream. This suite pins the frozen §3
 * contract: pipe streamable bodies unbuffered, forward upstream headers (minus
 * hop-by-hop), propagate disconnect/cancel via an AbortController, and keep the
 * JSON buffered path byte-identical (an application/json body is NEVER piped).
 *
 * RED before Build: the new-behavior tests (SSE/binary/stream:true/headers/abort/
 * cancel/fragmentation) fail because the route still json()-buffers and passes no
 * signal to fetch. The preservation tests (json byte-identical / no-cookie / 401 /
 * 204 / json-error-stays-buffered) are green-by-design and guard the unchanged path.
 *
 * Runs in the "bff" vitest project (route-handler idiom: call the handler directly
 * with a constructed NextRequest + msw stubbing http://gateway.test).
 */

import { describe, it, expect } from "vitest";
import { NextRequest } from "next/server";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { VALID_SESSION_JWT } from "./mocks/handlers";

import { GET, POST, DELETE } from "@/app/api/gw/[...path]/route";

// ── helpers ───────────────────────────────────────────────────────────────────
const enc = new TextEncoder();
const tick = () => new Promise((r) => setTimeout(r, 0));

/** A finite ReadableStream of text chunks (so the current json()-buffer path
 *  resolves rather than hanging — it fails fast on the content-type assertion). */
function textStream(chunks: string[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

function byteStream(bytes: number[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(bytes));
      controller.close();
    },
  });
}

function ctx(path: string[]) {
  return { params: Promise.resolve({ path }) };
}

type Init = { body?: unknown; signal?: AbortSignal; cookie?: boolean };

function req(method: string, path: string, init: Init = {}): NextRequest {
  const { body, signal, cookie = true } = init;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (cookie) headers.Cookie = `ai_proxy_session=${VALID_SESSION_JWT}`;
  return new NextRequest(`http://localhost:3000/api/gw/${path}`, {
    method,
    headers,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    ...(signal ? { signal } : {}),
  });
}

const GW = "http://gateway.test";

// ════════════════════════════════════════════════════════════════════════════
describe("streaming-bff — §3 streaming-proxy contract", () => {
  // ── M1: streamable bodies are piped unbuffered ────────────────────────────
  it("test_sse_streams_incrementally", async () => {
    server.use(
      http.post(`${GW}/v1/chat/completions`, () =>
        new HttpResponse(textStream(["data: a\n\n", "data: b\n\n", "data: [DONE]\n\n"]), {
          headers: { "Content-Type": "text/event-stream" },
        }),
      ),
    );

    const res = await POST(
      req("POST", "v1/chat/completions", { body: { stream: true } }),
      ctx(["v1", "chat", "completions"]),
    );

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toMatch(/text\/event-stream/);
    const text = await res.text();
    expect(text).toContain("data: a");
    expect(text).toContain("[DONE]");
  });

  it("test_binary_audio_streams", async () => {
    server.use(
      http.post(`${GW}/v1/audio/speech`, () =>
        new HttpResponse(byteStream([0x49, 0x44, 0x33]), {
          headers: { "Content-Type": "audio/mpeg" },
        }),
      ),
    );

    const res = await POST(
      req("POST", "v1/audio/speech", { body: { input: "hi" } }),
      ctx(["v1", "audio", "speech"]),
    );

    expect(res.headers.get("content-type")).toMatch(/audio\/mpeg/);
    const buf = new Uint8Array(await res.arrayBuffer());
    expect(Array.from(buf.slice(0, 3))).toEqual([0x49, 0x44, 0x33]);
  });

  it("test_stream_true_nonjson_is_piped", async () => {
    // upstream returns a NON-application/json type AND the request asked stream:true
    server.use(
      http.post(`${GW}/v1/x`, () =>
        new HttpResponse(textStream(["hello"]), { headers: { "Content-Type": "text/plain" } }),
      ),
    );

    const res = await POST(req("POST", "v1/x", { body: { stream: true } }), ctx(["v1", "x"]));

    expect(res.headers.get("content-type")).toMatch(/text\/plain/);
    expect(await res.text()).toBe("hello");
  });

  // ── M4/M6: the critical guard — JSON errors are NEVER piped ────────────────
  it("test_json_error_to_stream_request_stays_buffered", async () => {
    server.use(
      http.post(`${GW}/v1/chat/completions`, () =>
        HttpResponse.json({ code: "ERR_BUDGET_EXCEEDED" }, { status: 402 }),
      ),
    );

    const res = await POST(
      req("POST", "v1/chat/completions", { body: { stream: true } }),
      ctx(["v1", "chat", "completions"]),
    );

    expect(res.status).toBe(402);
    expect(res.headers.get("content-type")).toMatch(/application\/json/);
    expect((await res.json()) as { code: string }).toEqual({ code: "ERR_BUDGET_EXCEEDED" });
  });

  // ── M2: disconnect + cancel propagate to the upstream ──────────────────────
  it("test_client_disconnect_aborts_upstream", async () => {
    const ac = new AbortController();
    let upstreamSignal: AbortSignal | undefined;
    server.use(
      http.post(`${GW}/v1/chat/completions`, ({ request }) => {
        upstreamSignal = request.signal;
        return new HttpResponse(textStream(["data: a\n\n"]), {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );

    const res = await POST(
      req("POST", "v1/chat/completions", { body: { stream: true }, signal: ac.signal }),
      ctx(["v1", "chat", "completions"]),
    );
    expect(res.status).toBe(200);

    expect(upstreamSignal).toBeDefined();
    expect(upstreamSignal?.aborted).toBe(false);
    ac.abort(); // client disconnect
    await tick();
    expect(upstreamSignal?.aborted).toBe(true); // controller propagated the abort
  });

  it("test_response_cancel_aborts_upstream", async () => {
    let upstreamSignal: AbortSignal | undefined;
    server.use(
      http.post(`${GW}/v1/chat/completions`, ({ request }) => {
        upstreamSignal = request.signal;
        return new HttpResponse(textStream(["data: a\n\n", "data: b\n\n"]), {
          headers: { "Content-Type": "text/event-stream" },
        });
      }),
    );

    const res = await POST(
      req("POST", "v1/chat/completions", { body: { stream: true } }),
      ctx(["v1", "chat", "completions"]),
    );

    await res.body?.cancel(); // client cancels the response stream
    await tick();
    expect(upstreamSignal?.aborted).toBe(true);
  });

  it("test_pre_aborted_signal_returns_gracefully_no_crash", async () => {
    // Client already disconnected before the upstream fetch resolves: the handler
    // must NOT throw an unhandled AbortError (→ 500); it returns a graceful 499.
    const ac = new AbortController();
    ac.abort();
    server.use(
      http.post(`${GW}/v1/chat/completions`, () =>
        new HttpResponse(textStream(["data: a\n\n"]), {
          headers: { "Content-Type": "text/event-stream" },
        }),
      ),
    );

    const res = await POST(
      req("POST", "v1/chat/completions", { body: { stream: true }, signal: ac.signal }),
      ctx(["v1", "chat", "completions"]),
    );

    expect(res).toBeInstanceOf(Response);
    expect(res.status).toBe(499);
  });

  // ── M3: streamed responses forward upstream headers minus hop-by-hop ───────
  it("test_streamed_response_forwards_headers", async () => {
    server.use(
      http.post(`${GW}/v1/chat/completions`, () =>
        new HttpResponse(textStream(["data: a\n\n"]), {
          headers: {
            "Content-Type": "text/event-stream",
            "X-Request-Id": "rid-123",
            "Content-Length": "999",
            Connection: "keep-alive",
          },
        }),
      ),
    );

    const res = await POST(
      req("POST", "v1/chat/completions", { body: { stream: true } }),
      ctx(["v1", "chat", "completions"]),
    );

    expect(res.headers.get("x-request-id")).toBe("rid-123");
    expect(res.headers.get("cache-control")).toBe("no-cache");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    // hop-by-hop dropped
    expect(res.headers.get("content-length")).toBeNull();
    expect(res.headers.get("connection")).toBeNull();
  });

  // ── M4: non-streamable JSON stays byte-identical (preservation) ────────────
  it("test_nonstreamable_json_byte_identical", async () => {
    server.use(http.get(`${GW}/admin/usage`, () => HttpResponse.json({ a: 1 })));

    const res = await GET(req("GET", "admin/usage", { body: undefined }), ctx(["admin", "usage"]));

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toMatch(/application\/json/);
    expect((await res.json()) as { a: number }).toEqual({ a: 1 });
  });

  // ── M5 / Reject: no cookie → no upstream ───────────────────────────────────
  it("test_no_cookie_rejects_no_upstream", async () => {
    let called = false;
    server.use(
      http.get(`${GW}/admin/usage`, () => {
        called = true;
        return HttpResponse.json({});
      }),
    );

    const res = await GET(
      req("GET", "admin/usage", { body: undefined, cookie: false }),
      ctx(["admin", "usage"]),
    );

    expect(res.status).toBe(401);
    expect((await res.json()) as { code: string }).toEqual({ code: "ERR_AUTH_NO_SESSION" });
    expect(called).toBe(false);
  });

  // ── M4/M6 / Reject: upstream 401 clears the cookie ─────────────────────────
  it("test_upstream_401_clears_cookie", async () => {
    server.use(http.get(`${GW}/admin/usage`, () => HttpResponse.json({}, { status: 401 })));

    const res = await GET(req("GET", "admin/usage", { body: undefined }), ctx(["admin", "usage"]));

    expect(res.status).toBe(401);
    expect((await res.json()) as { code: string }).toEqual({ code: "ERR_AUTH_SESSION_EXPIRED" });
    expect(res.headers.get("set-cookie") ?? "").toMatch(/ai_proxy_session=;/);
  });

  // ── M4 edge: 204 stays empty ───────────────────────────────────────────────
  it("test_204_empty", async () => {
    server.use(http.delete(`${GW}/admin/keys/k1`, () => new HttpResponse(null, { status: 204 })));

    const res = await DELETE(
      req("DELETE", "admin/keys/k1", { body: undefined }),
      ctx(["admin", "keys", "k1"]),
    );

    expect(res.status).toBe(204);
  });

  // ── M1 edge: fragmentation — bytes pass through in order, no reframing ─────
  it("test_event_stream_chunk_fragmentation", async () => {
    server.use(
      http.post(`${GW}/v1/chat/completions`, () =>
        new HttpResponse(
          textStream(["da", "ta: hel", "lo\n", "\n", "data: [DO", "NE]\n\n"]),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
    );

    const res = await POST(
      req("POST", "v1/chat/completions", { body: { stream: true } }),
      ctx(["v1", "chat", "completions"]),
    );

    expect(res.headers.get("content-type")).toMatch(/text\/event-stream/);
    expect(await res.text()).toBe("data: hello\n\ndata: [DONE]\n\n");
  });
});
