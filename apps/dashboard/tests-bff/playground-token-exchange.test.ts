/**
 * tests-bff/playground-token-exchange.test.ts
 *
 * The BFF gateway proxy performs a SERVER-SIDE token exchange for the /v1 data
 * plane: it mints a short-lived playground sk- key (POST
 * /admin/keys/playground-token, with the session JWT) and forwards THAT key — not
 * the JWT — to /v1. The minted key is cached and reused; control-plane (/admin/*)
 * requests are unaffected (they keep using the JWT). The key never reaches the
 * browser. A data-plane 401 invalidates the cache so the next request re-mints.
 */

import { describe, it, expect, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { VALID_SESSION_JWT } from "./mocks/handlers";
import { POST as proxyPost, GET as proxyGet } from "@/app/api/gw/[...path]/route";
import { __resetPlaygroundTokenCache } from "@/lib/playground-token";

const GW = "http://gateway.test";
const PLAYGROUND_KEY = "sk-deadbeef.playgroundsecret";

function futureIso(minutes: number): string {
  return new Date(Date.now() + minutes * 60_000).toISOString();
}

function dataPlaneReq(): NextRequest {
  return new NextRequest("http://localhost:3000/api/gw/v1/chat/completions", {
    method: "POST",
    headers: { Cookie: `ai_proxy_session=${VALID_SESSION_JWT}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model: "openai/gpt-4o", messages: [{ role: "user", content: "hi" }] }),
  });
}

beforeEach(() => {
  __resetPlaygroundTokenCache();
});

describe("BFF playground token exchange", () => {
  it("test_v1_request_mints_and_forwards_playground_key_not_jwt", async () => {
    let mintAuth: string | null = null;
    let forwardedAuth: string | null = null;
    server.use(
      http.post(`${GW}/admin/keys/playground-token`, ({ request }) => {
        mintAuth = request.headers.get("Authorization");
        return HttpResponse.json({ key: PLAYGROUND_KEY, expires_at: futureIso(30) }, { status: 201 });
      }),
      http.post(`${GW}/v1/chat/completions`, ({ request }) => {
        forwardedAuth = request.headers.get("Authorization");
        return HttpResponse.json({ ok: true });
      }),
    );

    const res = await proxyPost(dataPlaneReq(), {
      params: Promise.resolve({ path: ["v1", "chat", "completions"] }),
    });

    expect(res.status).toBe(200);
    // The mint call carried the session JWT…
    expect(mintAuth).toBe(`Bearer ${VALID_SESSION_JWT}`);
    // …but the /v1 call carried the minted sk- key, NOT the JWT.
    expect(forwardedAuth).toBe(`Bearer ${PLAYGROUND_KEY}`);
    // …and the key never leaks back to the browser.
    expect(await res.text()).not.toContain(PLAYGROUND_KEY);
  });

  it("test_minted_key_is_cached_one_mint_for_two_requests", async () => {
    let mintCount = 0;
    server.use(
      http.post(`${GW}/admin/keys/playground-token`, () => {
        mintCount += 1;
        return HttpResponse.json({ key: PLAYGROUND_KEY, expires_at: futureIso(30) }, { status: 201 });
      }),
      http.post(`${GW}/v1/chat/completions`, () => HttpResponse.json({ ok: true })),
    );

    await proxyPost(dataPlaneReq(), { params: Promise.resolve({ path: ["v1", "chat", "completions"] }) });
    await proxyPost(dataPlaneReq(), { params: Promise.resolve({ path: ["v1", "chat", "completions"] }) });

    expect(mintCount).toBe(1);
  });

  it("test_control_plane_request_uses_jwt_no_mint", async () => {
    let minted = false;
    let adminAuth: string | null = null;
    server.use(
      http.post(`${GW}/admin/keys/playground-token`, () => {
        minted = true;
        return HttpResponse.json({ key: PLAYGROUND_KEY }, { status: 201 });
      }),
      http.get(`${GW}/admin/keys`, ({ request }) => {
        adminAuth = request.headers.get("Authorization");
        return HttpResponse.json([]);
      }),
    );

    const req = new NextRequest("http://localhost:3000/api/gw/admin/keys", {
      method: "GET",
      headers: { Cookie: `ai_proxy_session=${VALID_SESSION_JWT}` },
    });
    await proxyGet(req, { params: Promise.resolve({ path: ["admin", "keys"] }) });

    expect(minted).toBe(false);
    expect(adminAuth).toBe(`Bearer ${VALID_SESSION_JWT}`);
  });

  it("test_dataplane_401_invalidates_cache_and_remints", async () => {
    let mintCount = 0;
    let callCount = 0;
    server.use(
      http.post(`${GW}/admin/keys/playground-token`, () => {
        mintCount += 1;
        return HttpResponse.json({ key: PLAYGROUND_KEY, expires_at: futureIso(30) }, { status: 201 });
      }),
      http.post(`${GW}/v1/chat/completions`, () => {
        callCount += 1;
        // First call: the playground key is rejected (e.g. revoked) → 401.
        return callCount === 1
          ? HttpResponse.json({ code: "ERR_AUTH_KEY_INVALID" }, { status: 401 })
          : HttpResponse.json({ ok: true });
      }),
    );

    const first = await proxyPost(dataPlaneReq(), {
      params: Promise.resolve({ path: ["v1", "chat", "completions"] }),
    });
    // Data-plane 401 must NOT clear the session cookie.
    expect(first.status).toBe(401);
    const setCookie = first.headers.get("set-cookie") ?? first.headers.get("Set-Cookie");
    expect(setCookie ?? "").not.toContain("Max-Age=0");

    // Next request re-mints (cache was invalidated by the 401).
    await proxyPost(dataPlaneReq(), { params: Promise.resolve({ path: ["v1", "chat", "completions"] }) });
    expect(mintCount).toBe(2);
  });

  it("test_mint_failure_degrades_to_jwt_no_logout", async () => {
    let forwardedAuth: string | null = null;
    server.use(
      http.post(`${GW}/admin/keys/playground-token`, () =>
        HttpResponse.json({ code: "ERR" }, { status: 500 }),
      ),
      http.post(`${GW}/v1/chat/completions`, ({ request }) => {
        forwardedAuth = request.headers.get("Authorization");
        return HttpResponse.json({ code: "ERR_AUTH_KEY_INVALID" }, { status: 401 });
      }),
    );

    const res = await proxyPost(dataPlaneReq(), {
      params: Promise.resolve({ path: ["v1", "chat", "completions"] }),
    });
    // Honest degrade: mint failed → forwarded the JWT, surfaced the inline 401,
    // cookie intact (no logout).
    expect(forwardedAuth).toBe(`Bearer ${VALID_SESSION_JWT}`);
    expect(res.status).toBe(401);
    const setCookie = res.headers.get("set-cookie") ?? res.headers.get("Set-Cookie");
    expect(setCookie ?? "").not.toContain("Max-Age=0");
  });
});
