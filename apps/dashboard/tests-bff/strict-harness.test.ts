/**
 * tests-bff/strict-harness.test.ts — structural guards for the strict BFF test harness.
 *
 * These assert the FROZEN §3 invariant set for the v17 harness hardening:
 *   1. defaultHandlers ships NO `/api/gw/:path*` catch-all — a forgotten per-test gw handler
 *      must hit `onUnhandledRequest:"error"` and fail LOUDLY, not return silent wrong data.
 *   2. setup.ts keeps `onUnhandledRequest:"error"` (the loud-failure mode must not be loosened).
 *
 * RED before Build: the 4 wildcards are present in handlers.ts → guard (1) fails.
 * GREEN after Build: the wildcards are removed/scoped to concrete paths.
 *
 * This is a static/source suite (no rendering) — the harness invariant's "shape" is the
 * handler-registration + server-config source, so we assert against the file text.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const TESTS_BFF_ROOT = dirname(fileURLToPath(import.meta.url));

function source(rel: string): string {
  return readFileSync(join(TESTS_BFF_ROOT, rel), "utf8");
}

describe("strict BFF test harness — msw invariants", () => {
  it("test_no_gw_wildcard_in_default_handlers", () => {
    const handlers = source("mocks/handlers.ts");
    // No broad gw catch-all may remain as a REGISTERED handler in the shared defaults —
    // it silently defeats onUnhandledRequest:"error" (a forgotten per-test handler returns
    // wrong-but-quiet data instead of erroring). Scoped concrete gw paths are fine.
    // Match the actual handler-registration form `http.<method>(`…:path*`)` so a prose
    // comment mentioning the pattern does not trip the guard (false positive).
    expect(handlers).not.toMatch(/http\.[a-z]+\(\s*`[^`]*\/api\/gw\/[^`]*:path\*/);
  });

  it("test_unhandled_request_is_error", () => {
    const setup = source("setup.ts");
    // The loud-failure mode is the whole point of removing the wildcard — it must stay "error",
    // never be loosened to "warn"/"bypass".
    expect(setup).toMatch(/onUnhandledRequest:\s*["']error["']/);
  });
});
