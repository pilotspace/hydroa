/**
 * Patches Node.js require.cache so that require("next/navigation") returns
 * the SAME spy functions that vi.mock() in tests/setup.ts injected into the
 * ESM module registry.
 *
 * Also registers afterEach(vi.clearAllMocks) so spy call histories don't
 * bleed across tests (setup.ts resets MSW handlers and localStorage but
 * does not clear vi spies).
 *
 * Why the require patch: Vitest's vi.mock() intercepts ESM `import` but NOT
 * CJS `require()`. tests/mocks/next-navigation.ts uses require("next/navigation")
 * to access the mock router spies, so we mirror the ESM mock into require.cache.
 *
 * This file runs AFTER tests/setup.ts (listed second in setupFiles) so the
 * vi.mock factory is already registered when we dynamic-import the mock.
 */

import { vi, afterEach } from "vitest";
import Module from "module";

// ── Clear vi spy histories between tests ──────────────────────────────────────
// setup.ts resets MSW handlers and localStorage but does NOT clear vi.fn()
// call histories.  Without this, spy calls from test N bleed into test N+1.
afterEach(() => {
  vi.clearAllMocks();
});

// ── Mirror ESM vi.mock into Node require.cache ─────────────────────────────────
// Dynamic import returns the already-mocked module (vi.mock factory from
// setup.ts is already registered at this point in the setupFiles sequence).
const navMock = await import("next/navigation");

const navPath = require.resolve("next/navigation");
const distNavPath = require.resolve("next/dist/client/components/navigation");

interface NodeModuleWithCache {
  _cache: Record<string, { exports: unknown; filename: string; loaded: boolean }>;
  new (id: string): { filename: string; loaded: boolean; exports: unknown };
}

function patchCache(resolvedPath: string) {
  const M = Module as unknown as NodeModuleWithCache;
  const existing = M._cache?.[resolvedPath];
  if (existing) {
    existing.exports = navMock;
  } else {
    const mod = new M(resolvedPath);
    mod.filename = resolvedPath;
    mod.loaded = true;
    mod.exports = navMock;
    M._cache[resolvedPath] = mod;
  }
}

patchCache(navPath);
patchCache(distNavPath);
