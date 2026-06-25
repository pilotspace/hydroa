/**
 * Vitest global setup — runs once per worker via vitest.config.ts setupFiles.
 * Responsibilities:
 *   1. Extend expect with @testing-library/jest-dom matchers
 *   2. Start the msw Node server before all tests, reset handlers after each,
 *      close after all
 *   3. Clear localStorage before each test
 *   4. Provide a mock for next/navigation so router.push / redirect are
 *      spyable (tests assert router.push("/app/keys") etc.)
 *
 * Node 25 ships its own built-in `localStorage` global (used for its web-API
 * support) that is NOT the full Storage interface (no .clear(), .key(), etc.
 * unless --localstorage-file is provided).  Vitest's jsdom environment injects
 * window.localStorage but Node 25's global takes precedence in the worker
 * scope.  We replace the global with a proper in-memory Storage implementation
 * via vi.stubGlobal so that all test code and the afterEach cleanup use the
 * same full-featured object.
 */

import "@testing-library/jest-dom";
import { beforeAll, afterAll, afterEach, vi } from "vitest";
import { server } from "./mocks/server";

// ── in-memory Storage polyfill (replaces Node 25's partial localStorage) ─────
class InMemoryStorage implements Storage {
  private store: Record<string, string> = {};

  get length(): number {
    return Object.keys(this.store).length;
  }
  key(index: number): string | null {
    return Object.keys(this.store)[index] ?? null;
  }
  getItem(key: string): string | null {
    return Object.prototype.hasOwnProperty.call(this.store, key)
      ? this.store[key]
      : null;
  }
  setItem(key: string, value: string): void {
    this.store[key] = String(value);
  }
  removeItem(key: string): void {
    delete this.store[key];
  }
  clear(): void {
    this.store = {};
  }
}

const _localStorage = new InMemoryStorage();
// Override the global so both `localStorage` bare name and `window.localStorage`
// resolve to our full-featured implementation.
vi.stubGlobal("localStorage", _localStorage);

// ── msw server lifecycle ──────────────────────────────────────────────────────
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ── localStorage reset ────────────────────────────────────────────────────────
afterEach(() => {
  _localStorage.clear();
});

// ── next/navigation mock ──────────────────────────────────────────────────────
// Replaced at module-level so every test file that imports next/navigation
// receives the spies automatically.  Tests that need to inspect calls can
// import { mockRouter } from "./mocks/next-navigation".
vi.mock("next/navigation", () => {
  const push = vi.fn();
  const replace = vi.fn();
  const redirect = vi.fn();
  const useRouter = vi.fn(() => ({ push, replace, back: vi.fn() }));
  const usePathname = vi.fn(() => "/");
  const useSearchParams = vi.fn(() => new URLSearchParams());
  return { useRouter, usePathname, useSearchParams, redirect, push, replace };
});

// ── env defaults ──────────────────────────────────────────────────────────────
// lib/api-client will read process.env.NEXT_PUBLIC_GATEWAY_URL at runtime.
// Set it before any test module is evaluated.
process.env.NEXT_PUBLIC_GATEWAY_URL = "http://gateway.test";
