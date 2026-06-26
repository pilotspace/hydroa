/**
 * tests-bff/setup.ts — vitest global setup for the BFF test suite
 *
 * Mirrors tests/setup.ts idioms but targets same-origin /api/* routes
 * instead of direct GATEWAY_URL calls.
 *
 * Key differences from the v1 setup:
 *   - msw handlers intercept BOTH http://gateway.test (for BFF server-side
 *     fetch simulation) AND same-origin http://localhost:3000/api/* (for
 *     client-side bff-client.ts fetch calls in component tests)
 *   - localStorage is still polyfilled (components may still use it during
 *     the transition; new code must NOT write "ai_proxy_token")
 *   - next/headers cookies() is mocked so route handler unit tests can
 *     inject the ai_proxy_session cookie without a running Next.js server
 */

import "@testing-library/jest-dom";
import { beforeAll, afterAll, afterEach, vi } from "vitest";
import { server } from "./mocks/server";
import { __resetBreakers } from "@/lib/resilient-fetch";

// ── in-memory Storage polyfill ────────────────────────────────────────────────
class InMemoryStorage implements Storage {
  private store: Record<string, string> = {};
  get length(): number { return Object.keys(this.store).length; }
  key(index: number): string | null { return Object.keys(this.store)[index] ?? null; }
  getItem(key: string): string | null {
    return Object.prototype.hasOwnProperty.call(this.store, key) ? this.store[key] : null;
  }
  setItem(key: string, value: string): void { this.store[key] = String(value); }
  removeItem(key: string): void { delete this.store[key]; }
  clear(): void { this.store = {}; }
}

const _localStorage = new InMemoryStorage();
vi.stubGlobal("localStorage", _localStorage);

// ── msw server lifecycle ──────────────────────────────────────────────────────
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ── localStorage reset ────────────────────────────────────────────────────────
afterEach(() => {
  _localStorage.clear();
});

// ── circuit-breaker reset (per-runtime state; clear between tests) ────────────
afterEach(() => {
  __resetBreakers();
});

// Instant retry backoff in tests (avoids real latency / flaky waitFor timeouts).
process.env.BFF_FETCH_BACKOFF_MS = "1";

// ── next/navigation mock ──────────────────────────────────────────────────────
vi.mock("next/navigation", () => {
  const push = vi.fn();
  const replace = vi.fn();
  const redirect = vi.fn();
  const useRouter = vi.fn(() => ({ push, replace, back: vi.fn() }));
  const usePathname = vi.fn(() => "/");
  const useSearchParams = vi.fn(() => new URLSearchParams());
  return { useRouter, usePathname, useSearchParams, redirect, push, replace };
});

// ── next/headers mock — allows route handler tests to inject cookies ──────────
// Route handlers call cookies().get("ai_proxy_session"). We mock the module
// so tests can control what the cookie store returns.
vi.mock("next/headers", () => {
  const cookieStore = {
    get: vi.fn((_name: string) => undefined as { name: string; value: string } | undefined),
    set: vi.fn(),
    delete: vi.fn(),
    has: vi.fn((_name: string) => false),
  };
  return {
    cookies: vi.fn(() => cookieStore),
    headers: vi.fn(() => new Headers()),
    _cookieStore: cookieStore,
  };
});

// ── env defaults ──────────────────────────────────────────────────────────────
// BFF route handlers read GATEWAY_URL (server-side). Set before module eval.
process.env.GATEWAY_URL = "http://gateway.test";
process.env.NEXT_PUBLIC_GATEWAY_URL = "http://gateway.test";
// Same-origin base for bff-client.ts component tests
process.env.NEXT_PUBLIC_APP_URL = "http://localhost:3000";
