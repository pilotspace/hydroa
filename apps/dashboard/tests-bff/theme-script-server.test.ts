/**
 * tests-bff/theme-script-server.test.ts (v24) — structural invariants of the theme no-flash
 * Server-Component split (v23 review nit #3).
 *
 * The frozen §3 MODULE/BOUNDARY shape: themeScript() must be callable from a Server Component, so
 * (a) it lives in a module with NO "use client" directive, (b) app/layout.tsx is a Server Component
 * (no "use client") that still renders <script>{themeScript()}</script> in <head>, and (c) the
 * client context (ThemeProvider + QueryClientProvider) moves to a "use client" Providers wrapper.
 *
 * This is a static/filesystem suite (no rendering) — the fix is a boundary relocation, not a
 * behavioral feature, so its "shape" is the directive + import layout. RED before Build:
 * app/layout.tsx has "use client"; app/providers.tsx and components/ui/theme-script.ts are absent.
 * GREEN after the split. (The pre-paint behavior + a clean build are the verify-phase gate.)
 */
import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const DASHBOARD_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel: string) => readFileSync(join(DASHBOARD_ROOT, rel), "utf8");

/** A "use client" directive must be the first statement (quotes either style, optional semicolon). */
function hasUseClientDirective(src: string): boolean {
  // strip a leading block/line comment + blank lines, then check the first non-trivial line
  const firstCode = src
    .replace(/^﻿/, "")
    .split("\n")
    .map((l) => l.trim())
    .find((l) => l.length > 0 && !l.startsWith("//") && !l.startsWith("/*") && !l.startsWith("*"));
  return firstCode === '"use client";' || firstCode === "'use client';";
}

describe("theme no-flash — Server-Component layout", () => {
  it("test_layout_is_server_component", () => {
    const layout = read("app/layout.tsx");
    expect(hasUseClientDirective(layout)).toBe(false);
    // it still renders the no-flash script in <head> from server code
    expect(layout).toContain("themeScript()");
    expect(layout).toMatch(/<head>[\s\S]*<script>\{themeScript\(\)\}<\/script>[\s\S]*<\/head>/);
    // and delegates the client context to the Providers wrapper
    expect(layout).toContain("Providers");
  });

  it("test_providers_is_client_wrapper", () => {
    expect(existsSync(join(DASHBOARD_ROOT, "app/providers.tsx"))).toBe(true);
    const providers = read("app/providers.tsx");
    expect(hasUseClientDirective(providers)).toBe(true);
    expect(providers).toContain("ThemeProvider");
    expect(providers).toContain("QueryClientProvider");
    expect(providers).toMatch(/export function Providers/);
  });

  it("test_themescript_lives_in_a_non_client_module", () => {
    const modPath = "components/ui/theme-script.ts";
    expect(existsSync(join(DASHBOARD_ROOT, modPath))).toBe(true);
    const mod = read(modPath);
    expect(hasUseClientDirective(mod)).toBe(false);
    expect(mod).toMatch(/export function themeScript/);
  });

  it("test_barrel_reexports_themescript_from_server_module", () => {
    const barrel = read("components/ui/index.ts");
    // the barrel still exposes themeScript, now sourced from the server-safe module
    expect(barrel).toContain("themeScript");
    expect(barrel).toMatch(/from\s+["']\.\/theme-script["']/);
  });

  it("test_theme_provider_no_longer_defines_themescript", () => {
    // single source of truth: themeScript is defined once, in the server-safe module
    const provider = read("components/ui/theme-provider.tsx");
    expect(provider).not.toMatch(/export function themeScript/);
  });
});
