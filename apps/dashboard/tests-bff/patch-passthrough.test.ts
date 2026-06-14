/**
 * tests-bff/patch-passthrough.test.ts — RED suite for bff-patch-passthrough.
 *
 * The BFF authenticated proxy catch-all (app/api/gw/[...path]/route.ts) exports
 * GET/POST/PUT/DELETE but NOT PATCH — a v13 latent bug: KeyGovernanceEditor's
 * `bffPatch /admin/keys/{id}` 405s in production (the v13 govern suite passed only
 * because msw intercepts the CLIENT fetch, never running the Next route). v15
 * team-budget (PATCH /admin/teams/{id}) needs the same verb.
 *
 * RED before build: `import { PATCH as proxyPatch }` from the route resolves to a
 * missing export → Vite throws "does not provide an export named 'PATCH'" at
 * collect, so this whole file is red until Build adds the wrapper.
 *
 * Runs in the "bff" vitest project (route-handler idiom: call the handler directly
 * with a constructed NextRequest + msw stubbing the upstream gateway).
 */

import { describe, it, expect } from "vitest";
import { NextRequest } from "next/server";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { VALID_SESSION_JWT } from "./mocks/handlers";

// ── RED: PATCH is not exported from the catch-all until Build adds it ───────────
import { PATCH as proxyPatch } from "@/app/api/gw/[...path]/route";

function patchWithCookie(url: string, jwt: string, body: unknown): NextRequest {
  return new NextRequest(url, {
    method: "PATCH",
    headers: { Cookie: `ai_proxy_session=${jwt}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("PATCH /api/gw/[...path]", () => {
  it("test_bff_proxy_patch_forwards_bearer_and_body", async () => {
    let capturedAuth: string | null = null;
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.patch("http://gateway.test/admin/keys/kid-1", async ({ request }) => {
        capturedAuth = request.headers.get("Authorization");
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ key_id: "kid-1", monthly_budget_usd: "5.00" });
      }),
    );

    const req = patchWithCookie(
      "http://localhost:3000/api/gw/admin/keys/kid-1",
      VALID_SESSION_JWT,
      { monthly_budget_usd: "5.00" },
    );

    const res = await proxyPatch(req, { params: Promise.resolve({ path: ["admin", "keys", "kid-1"] }) });

    // upstream received the bearer + the EXACT body (no drop)
    expect(capturedAuth).toBe(`Bearer ${VALID_SESSION_JWT}`);
    expect(capturedBody).toEqual({ monthly_budget_usd: "5.00" });

    // response proxied verbatim, JWT never leaked into the body
    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.key_id).toBe("kid-1");
    expect(JSON.stringify(body)).not.toContain(VALID_SESSION_JWT);
  });

  it("test_bff_proxy_patch_absent_cookie_401", async () => {
    let upstreamCalled = false;
    server.use(
      http.patch("http://gateway.test/admin/keys/kid-1", () => {
        upstreamCalled = true;
        return HttpResponse.json({});
      }),
    );

    const req = new NextRequest("http://localhost:3000/api/gw/admin/keys/kid-1", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ monthly_budget_usd: "5.00" }),
    });

    const res = await proxyPatch(req, { params: Promise.resolve({ path: ["admin", "keys", "kid-1"] }) });

    expect(res.status).toBe(401);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_NO_SESSION");
    expect(upstreamCalled).toBe(false);
  });
});
