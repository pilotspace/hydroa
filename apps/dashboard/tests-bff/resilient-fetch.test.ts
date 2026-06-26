/**
 * tests-bff/resilient-fetch.test.ts — v50 resilient-bff-fetch
 *
 * Red suite for the hardened BFF fetch core (lib/resilient-fetch.ts) plus its
 * adapter delegation (lib/bff-client.ts) and server proxy hardening
 * (app/api/gw/[...path]/route.ts).
 *
 * RED failure mode (pre-build):
 *   - import from "@/lib/resilient-fetch" fails MODULE_NOT_FOUND
 *   - bffAuthGet is not yet exported from "@/lib/bff-client"
 *   - the gw route does not yet map an upstream timeout to 504
 *
 * Assertions are behavioral: thrown BffError codes/status, upstream attempt
 * counts, and Response bodies — never internal breaker fields.
 */

import { describe, it, expect, beforeEach, beforeAll } from "vitest";
import { http, HttpResponse, delay } from "msw";
import { NextRequest } from "next/server";
import { server } from "./mocks/server";

import {
  resilientFetch,
  __resetBreakers,
  __setBreakerConfigForTest,
} from "@/lib/resilient-fetch";
import { bffGet, bffPost, bffAuthGet, BffError } from "@/lib/bff-client";
import { GET as gwGET } from "@/app/api/gw/[...path]/route";

// Make retry backoff effectively instant so the suite stays fast.
beforeAll(() => {
  process.env.BFF_FETCH_BACKOFF_MS = "1";
});

beforeEach(() => {
  __resetBreakers();
  // Generous defaults; individual breaker tests tighten via the seam.
  __setBreakerConfigForTest({ failureThreshold: 5, cooldownMs: 30_000 });
});

// ── M1 timeout ────────────────────────────────────────────────────────────────
describe("resilientFetch: timeout", () => {
  it("test_timeout_aborts", async () => {
    server.use(
      http.get("http://svc.test/slow", async () => {
        await delay(200);
        return HttpResponse.json({ ok: true });
      })
    );

    await expect(
      resilientFetch("http://svc.test/slow", { method: "GET" }, { timeoutMs: 10, maxRetries: 0 })
    ).rejects.toMatchObject({ status: 504 });

    // and the thrown error carries the timeout code
    await resilientFetch("http://svc.test/slow", { method: "GET" }, { timeoutMs: 10, maxRetries: 0 }).catch(
      (e: BffError) => {
        expect(e).toBeInstanceOf(BffError);
        expect(e.problem.code).toBe("ERR_BFF_TIMEOUT");
      }
    );
  });
});

// ── M2 bounded idempotent retry ────────────────────────────────────────────────
describe("resilientFetch: retry", () => {
  it("test_get_retries_then_succeeds", async () => {
    let attempts = 0;
    server.use(
      http.get("http://svc.test/flaky", () => {
        attempts += 1;
        if (attempts === 1) return HttpResponse.json({ err: true }, { status: 503 });
        return HttpResponse.json({ ok: true });
      })
    );

    const res = await resilientFetch(
      "http://svc.test/flaky",
      { method: "GET" },
      { maxRetries: 2 }
    );
    const body = (await res.json()) as { ok: boolean };

    expect(body.ok).toBe(true);
    expect(attempts).toBe(2); // one retry — within cap+1
  });

  it("test_network_exhausts_to_502", async () => {
    let attempts = 0;
    server.use(
      http.get("http://svc.test/down", () => {
        attempts += 1;
        return HttpResponse.error(); // network-level failure
      })
    );

    await expect(
      resilientFetch("http://svc.test/down", { method: "GET" }, { maxRetries: 2 })
    ).rejects.toMatchObject({ status: 502, problem: { code: "ERR_BFF_NETWORK" } });

    expect(attempts).toBe(3); // cap + 1, no unbounded loop
  });

  it("test_429_surfaced_not_retried", async () => {
    let attempts = 0;
    server.use(
      http.get("http://localhost:3000/api/gw/admin/keys", () => {
        attempts += 1;
        return HttpResponse.json({ title: "rate limited", status: 429 }, { status: 429 });
      })
    );

    await expect(bffGet("/admin/keys")).rejects.toMatchObject({ status: 429 });
    expect(attempts).toBe(1); // surfaced, never retried (no herd)
  });
});

// ── M2b non-idempotent POST is never retried ────────────────────────────────────
describe("resilientFetch: non-idempotent", () => {
  it("test_post_never_retried", async () => {
    let attempts = 0;
    server.use(
      http.post("http://localhost:3000/api/gw/admin/keys", () => {
        attempts += 1;
        return HttpResponse.error();
      })
    );

    await expect(bffPost("/admin/keys", { name: "k" })).rejects.toBeInstanceOf(BffError);
    expect(attempts).toBe(1); // exactly one attempt — no double-apply
  });
});

// ── M3 circuit-breaker ──────────────────────────────────────────────────────────
describe("resilientFetch: circuit-breaker", () => {
  it("test_circuit_opens_fail_fast", async () => {
    __setBreakerConfigForTest({ failureThreshold: 2, cooldownMs: 30_000 });
    let attempts = 0;
    server.use(
      http.get("http://breaker.test/x", () => {
        attempts += 1;
        return HttpResponse.error();
      })
    );

    // Drive 2 consecutive failures (threshold) — no retry to keep counting clean.
    await resilientFetch("http://breaker.test/x", { method: "GET" }, { maxRetries: 0 }).catch(() => {});
    await resilientFetch("http://breaker.test/x", { method: "GET" }, { maxRetries: 0 }).catch(() => {});
    const attemptsBeforeOpen = attempts;

    // Next call must fail fast WITHOUT touching the network.
    await expect(
      resilientFetch("http://breaker.test/x", { method: "GET" }, { maxRetries: 0 })
    ).rejects.toMatchObject({ status: 503, problem: { code: "ERR_BFF_CIRCUIT_OPEN" } });

    expect(attempts).toBe(attemptsBeforeOpen); // fetch not invoked while open
  });

  it("test_circuit_halfopen_recovers", async () => {
    __setBreakerConfigForTest({ failureThreshold: 2, cooldownMs: 30 });
    let mode: "fail" | "ok" = "fail";
    server.use(
      http.get("http://recover.test/y", () => {
        if (mode === "fail") return HttpResponse.error();
        return HttpResponse.json({ ok: true });
      })
    );

    await resilientFetch("http://recover.test/y", { method: "GET" }, { maxRetries: 0 }).catch(() => {});
    await resilientFetch("http://recover.test/y", { method: "GET" }, { maxRetries: 0 }).catch(() => {});

    // Circuit open now; wait out the cooldown, then recover.
    await new Promise((r) => setTimeout(r, 50));
    mode = "ok";

    const res = await resilientFetch("http://recover.test/y", { method: "GET" }, { maxRetries: 0 });
    expect(res.ok).toBe(true);
  });

  it("test_circuit_halfopen_single_probe", async () => {
    // Under parallel requests, exactly ONE half-open trial may touch the network.
    __setBreakerConfigForTest({ failureThreshold: 1, cooldownMs: 20 });
    let mode: "fail" | "slow" = "fail";
    let probes = 0;
    server.use(
      http.get("http://probe.test/q", async () => {
        if (mode === "fail") return HttpResponse.error();
        probes += 1;
        await delay(40);
        return HttpResponse.json({ ok: true });
      })
    );

    // Open the circuit (1 failure).
    await resilientFetch("http://probe.test/q", { method: "GET" }, { maxRetries: 0 }).catch(() => {});

    // Wait out the cooldown, then switch to a slow success and fire two at once.
    await new Promise((r) => setTimeout(r, 30));
    mode = "slow";
    const [r1, r2] = await Promise.allSettled([
      resilientFetch("http://probe.test/q", { method: "GET" }, { maxRetries: 0 }),
      resilientFetch("http://probe.test/q", { method: "GET" }, { maxRetries: 0 }),
    ]);

    expect(probes).toBe(1); // only one trial reached the network
    const codes = [r1, r2].map((r) =>
      r.status === "rejected" ? (r.reason as BffError).problem.code : "ok"
    );
    expect(codes.filter((c) => c === "ok").length).toBe(1);
    expect(codes.filter((c) => c === "ERR_BFF_CIRCUIT_OPEN").length).toBe(1);
  });

  it("test_per_target_isolation", async () => {
    __setBreakerConfigForTest({ failureThreshold: 1, cooldownMs: 30_000 });
    server.use(
      http.get("http://a.test/p", () => HttpResponse.error()),
      http.get("http://b.test/p", () => HttpResponse.json({ ok: true }))
    );

    // Open A's circuit.
    await resilientFetch("http://a.test/p", { method: "GET" }, { maxRetries: 0 }).catch(() => {});
    await expect(
      resilientFetch("http://a.test/p", { method: "GET" }, { maxRetries: 0 })
    ).rejects.toMatchObject({ problem: { code: "ERR_BFF_CIRCUIT_OPEN" } });

    // B (different origin) is unaffected.
    const res = await resilientFetch("http://b.test/p", { method: "GET" }, { maxRetries: 0 });
    expect(res.ok).toBe(true);
  });
});

// ── M4 byte-identical public surface ────────────────────────────────────────────
describe("bff-client: byte-identical surface", () => {
  it("test_public_api_byte_identical", async () => {
    server.use(
      http.get("http://localhost:3000/api/gw/admin/keys", () =>
        HttpResponse.json([{ key_id: "k1" }])
      )
    );
    const result = await bffGet<Array<{ key_id: string }>>("/admin/keys");
    expect(result[0].key_id).toBe("k1");
  });

  it("test_bff_auth_get_hits_auth_path_direct", async () => {
    // The migrated apiGet("/api/auth/me") -> bffAuthGet("me") must hit
    // /api/auth/me directly (NOT /api/gw/...).
    let hit = false;
    server.use(
      http.get("http://localhost:3000/api/auth/me", () => {
        hit = true;
        return HttpResponse.json({ user_id: "u1", role: "owner" });
      })
    );
    const me = await bffAuthGet<{ user_id: string; role: string }>("me");
    expect(hit).toBe(true);
    expect(me.role).toBe("owner");
  });
});

// ── M5 server proxy maps upstream timeout to 504 ────────────────────────────────
describe("gw proxy: upstream timeout", () => {
  it("test_proxy_maps_timeout_504", async () => {
    const prev = process.env.GATEWAY_PROXY_TIMEOUT_MS;
    process.env.GATEWAY_PROXY_TIMEOUT_MS = "20";
    try {
      server.use(
        http.get("http://gateway.test/admin/keys", async () => {
          await delay(200);
          return HttpResponse.json({ ok: true });
        })
      );

      const req = new NextRequest("http://localhost:3000/api/gw/admin/keys", {
        method: "GET",
        headers: { Cookie: "ai_proxy_session=tok" },
      });
      const ctx = { params: Promise.resolve({ path: ["admin", "keys"] }) };

      const res = await gwGET(req, ctx);

      // Maps upstream timeout to a typed 504 — NOT an unhandled 500.
      expect(res.status).toBe(504);
      const body = (await res.json()) as { code?: string };
      expect(body.code).toBe("ERR_BFF_TIMEOUT");
    } finally {
      if (prev === undefined) delete process.env.GATEWAY_PROXY_TIMEOUT_MS;
      else process.env.GATEWAY_PROXY_TIMEOUT_MS = prev;
    }
  });
});

// ── M6 fail-open when breaker config unreadable ─────────────────────────────────
describe("resilientFetch: fail-open", () => {
  it("test_failopen_when_no_config", async () => {
    // An unparseable threshold must not throw config errors — degrade to a
    // single direct attempt that still reaches the network.
    __setBreakerConfigForTest({ failureThreshold: Number.NaN, cooldownMs: Number.NaN });
    let hit = false;
    server.use(
      http.get("http://failopen.test/z", () => {
        hit = true;
        return HttpResponse.json({ ok: true });
      })
    );

    const res = await resilientFetch("http://failopen.test/z", { method: "GET" }, { maxRetries: 0 });
    expect(hit).toBe(true);
    expect(res.ok).toBe(true);
  });
});
