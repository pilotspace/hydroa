/**
 * tests-bff/gw-body-guard.test.ts — v50 bff-input-validation (gw size guard)
 *
 * The generic /api/gw/[...path] proxy caps request body size: a body over the
 * cap is rejected 413 ERR_BFF_PAYLOAD_TOO_LARGE BEFORE any upstream call; a
 * within-limit body forwards unchanged (task-1 timeout/breaker intact).
 *
 * RED before build: the proxy has no size guard, so an oversized POST forwards
 * (returns 200) instead of 413.
 */

import { describe, it, expect, afterEach } from "vitest";
import { NextRequest } from "next/server";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { VALID_SESSION_JWT } from "./mocks/handlers";
import { POST as proxyPost, PATCH as proxyPatch } from "@/app/api/gw/[...path]/route";

function postWithCookie(url: string, body: string): NextRequest {
  return new NextRequest(url, {
    method: "POST",
    headers: { Cookie: `ai_proxy_session=${VALID_SESSION_JWT}`, "Content-Type": "application/json" },
    body,
  });
}

afterEach(() => {
  delete process.env.GW_MAX_BODY_BYTES;
});

describe("POST /api/gw/[...path] body-size guard", () => {
  it("test_gw_oversized_body_413_no_upstream", async () => {
    process.env.GW_MAX_BODY_BYTES = "50";
    let upstreamCalled = false;
    server.use(
      http.post("http://gateway.test/admin/keys", () => {
        upstreamCalled = true;
        return HttpResponse.json({ ok: true });
      }),
    );
    const req = postWithCookie(
      "http://localhost:3000/api/gw/admin/keys",
      JSON.stringify({ blob: "x".repeat(500) }),
    );
    const res = await proxyPost(req, { params: Promise.resolve({ path: ["admin", "keys"] }) });
    expect(res.status).toBe(413);
    expect((await res.json()).code).toBe("ERR_BFF_PAYLOAD_TOO_LARGE");
    expect(upstreamCalled).toBe(false);
  });

  it("test_gw_patch_oversized_body_413", async () => {
    process.env.GW_MAX_BODY_BYTES = "50";
    let upstreamCalled = false;
    server.use(
      http.patch("http://gateway.test/admin/keys/kid-1", () => {
        upstreamCalled = true;
        return HttpResponse.json({ ok: true });
      }),
    );
    const req = new NextRequest("http://localhost:3000/api/gw/admin/keys/kid-1", {
      method: "PATCH",
      headers: { Cookie: `ai_proxy_session=${VALID_SESSION_JWT}`, "Content-Type": "application/json" },
      body: JSON.stringify({ blob: "x".repeat(500) }),
    });
    const res = await proxyPatch(req, { params: Promise.resolve({ path: ["admin", "keys", "kid-1"] }) });
    expect(res.status).toBe(413);
    expect(upstreamCalled).toBe(false);
  });

  it("test_gw_small_body_forwards_unchanged", async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post("http://gateway.test/admin/keys", async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ key_id: "kid-1" });
      }),
    );
    const req = postWithCookie(
      "http://localhost:3000/api/gw/admin/keys",
      JSON.stringify({ name: "ci" }),
    );
    const res = await proxyPost(req, { params: Promise.resolve({ path: ["admin", "keys"] }) });
    expect(res.status).toBe(200);
    expect(capturedBody).toEqual({ name: "ci" });
    expect((await res.json()).key_id).toBe("kid-1");
  });
});
